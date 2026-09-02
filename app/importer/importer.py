"""统一导入入口：批次管理 / 事务写入 / 校验 / 原始文件存档

规则（已确认）：
- 覆盖式导入：同月同类型已有 active 批次 → 先撤销旧批次再导入
- 撤销导入：删除该批次所有数据（仅 import 来源），manual 数据不受影响
- 经办人必须命中职工花名册（"公共"等白名单除外），未命中阻止导入
- 台账 sheet1+sheet2 合计 = 该月销项价税合计合计（销项须先导入）
- 跨月重复发票：collection 以最新台账记录为准（先删该票 import 旧记录再插新）
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from app.db import get_conn
from app.engine.backfill import norm_type, staff_type_of
from app.engine.raw_ledger import SHEET_LABELS
from app.importer.expense_import import parse_expense_file
from app.importer.invoice_import import parse_invoice_file, parse_invoice_workbook
from app.importer.ledger_import import parse_ledger_file
from app.importer.salary_import import parse_salary_file
from app.importer.excel_reader import ImportError_

ARCHIVE_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "archive"

# 非员工经办人白名单（公共费用等）
HANDLER_WHITELIST = {"公共", "行政"}


def _staff_names(conn) -> set:
    return {r["name"] for r in conn.execute("SELECT name FROM staff WHERE is_active=1")}


def _archive_file(src: str, batch_type: str, period: str) -> str:
    """复制原始文件到存档目录，返回相对路径（源已在存档目录则跳过，避免自复制）"""
    src = Path(src)
    dest_dir = ARCHIVE_ROOT / batch_type / period
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return str(dest.relative_to(ARCHIVE_ROOT.parent.parent.parent))


def rollback_batch(conn, batch_id: int) -> None:
    """撤销一个导入批次（删除其写入的数据）

    规则：
    - 本批次创建的发票：级联删除其全部 charge_detail/collection（不限批次，
      因为跨批次可能引用，如 ledger 批次对销项发票的拆分）
    - 本批次对其他批次发票创建的 charge_detail/collection：按批次删除
    - refund / prepayment_offset 为手动数据，若有引用则阻止撤销
    """
    invs = [
        r["invoice_no"]
        for r in conn.execute("SELECT invoice_no FROM invoice WHERE import_batch_id=?", (batch_id,))
    ]
    for no in invs:
        if conn.execute("SELECT 1 FROM refund WHERE red_invoice_no=?", (no,)).fetchone():
            raise ImportError_(f"发票 {no} 存在已确认的退款记录，无法撤销导入。请先删除对应退款记录。")
        if conn.execute("SELECT 1 FROM prepayment_offset WHERE invoice_no=?", (no,)).fetchone():
            raise ImportError_(f"发票 {no} 存在预收款核销记录，无法撤销导入。请先删除核销记录。")

    # 本批次创建的发票：级联删除其全部引用数据（不限批次）
    for no in invs:
        conn.execute("DELETE FROM collection WHERE invoice_no=?", (no,))
        conn.execute("DELETE FROM charge_detail WHERE invoice_no=?", (no,))
    # 本批次对其他批次发票创建的数据：按批次删除
    conn.execute("DELETE FROM collection WHERE import_batch_id=?", (batch_id,))
    conn.execute("DELETE FROM charge_detail WHERE import_batch_id=?", (batch_id,))
    conn.execute("DELETE FROM prepayment WHERE import_batch_id=?", (batch_id,))
    conn.execute("DELETE FROM expense_ledger WHERE import_batch_id=?", (batch_id,))
    conn.execute("DELETE FROM invoice WHERE import_batch_id=?", (batch_id,))
    conn.execute("DELETE FROM staff WHERE import_batch_id=?", (batch_id,))
    conn.execute("UPDATE import_batch SET status='rolled_back' WHERE id=?", (batch_id,))


def _drop_active_batch(conn, batch_type: str, period: str) -> None:
    """同月同类型覆盖式导入：撤销旧 active 批次"""
    old = conn.execute(
        "SELECT id FROM import_batch WHERE batch_type=? AND period=? AND status='active'",
        (batch_type, period),
    ).fetchall()
    for r in old:
        rollback_batch(conn, r["id"])


def _new_batch(conn, batch_type: str, period: str, file_name: str, archive_path: str, file_hash: str) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "INSERT INTO import_batch (batch_type, period, file_name, archive_path, file_hash, imported_at) VALUES (?,?,?,?,?,?)",
        (batch_type, period, file_name, archive_path, file_hash, now),
    )
    return cur.lastrowid


def _raw_cell(row, header, *names) -> str:
    """从原始行按表头取某列文本（兼容多别名）。"""
    if not header or not row:
        return ""
    for n in names:
        try:
            i = header.index(n)
        except ValueError:
            continue
        if 0 <= i < len(row):
            return (row[i] or "").strip()
    return ""


def compute_expected_receipts(inv: Dict) -> Tuple[float, List[Dict]]:
    """从已解析发票推算「源声称收款」(方案E 快照 expected)。

    返回 (合计, [{"ym": "YYYY-MM", "amount": float}, ...])。
    红字=0；纯日期=全额于该日；逐期=各期金额累加(0 表示全额)。
    """
    total = inv.get("total_amount") or 0.0
    if inv.get("is_red"):
        return 0.0, []
    rem = inv.get("remark") or {}
    if rem.get("pure_date"):
        ym = (rem["pure_date"] or "")[:7]
        return total, [{"ym": ym, "amount": total}]
    if rem.get("receipts"):
        items: List[Dict] = []
        s = 0.0
        for ym, amt in rem["receipts"]:
            a = amt if amt > 0 else total
            s += a
            items.append({"ym": (ym or "")[:7], "amount": a})
        return s, items
    return 0.0, []


def _upsert_received_snapshot(conn, inv: Dict, batch_id: int) -> None:
    """方案E：把某发票的「源声称收款」与「本批次实际写入收款」落 received_snapshot。

    在 import 写库与「一键修正（按源重算）」后调用，保证快照与最新落库一致。
    失败仅告警，不阻断主流程。
    """
    try:
        no = inv["invoice_no"]
        exp_total, exp_items = compute_expected_receipts(inv)
        act_rows = conn.execute(
            "SELECT receipt_date, amount, person_name FROM collection "
            "WHERE source='import' AND import_batch_id=? AND invoice_no=?",
            (batch_id, no),
        ).fetchall()
        act_items = [
            {"ym": (r["receipt_date"] or "")[:7], "amount": r["amount"],
             "person": r["person_name"] or ""}
            for r in act_rows
        ]
        act_total = sum(it["amount"] for it in act_items)
        conn.execute(
            "INSERT OR REPLACE INTO received_snapshot "
            "(import_batch_id, invoice_no, expected_json, actual_json) VALUES (?,?,?,?)",
            (batch_id, no,
             json.dumps({"total": exp_total, "items": exp_items}, ensure_ascii=False),
             json.dumps({"total": act_total, "items": act_items}, ensure_ascii=False)),
        )
    except Exception as e:  # noqa: BLE001
        print(f"[received_snapshot] upsert failed for {inv.get('invoice_no')}: {e}")


def _write_collection_for_invoice(conn, inv: Dict, batch_id: int) -> None:
    """按发票写入收款明细（普通导入路径；split_receipts=问题行逐人收款）。

    逻辑与原写库主循环一致，抽出供导入与「导入校验-一键修正」复用。
    写库后同步更新 received_snapshot（方案E）。
    """
    no = inv["invoice_no"]
    sheet = inv.get("sheet_name") or ""
    row = inv.get("row_no") or 0
    conn.execute("DELETE FROM collection WHERE invoice_no=? AND source='import'", (no,))
    if "split_receipts" in inv:
        # 问题行修正：逐经办人写入收款（person_name 归因；空列表 = 无收款）
        for name, amt, ym in inv["split_receipts"]:
            conn.execute(
                "INSERT INTO collection (invoice_no, amount, receipt_date, person_name, source, import_batch_id, src_sheet, src_row) VALUES (?,?,?,?,?,?,?,?)",
                (no, amt, ym, name, "import", batch_id, sheet, row),
            )
    else:
        rem = inv["remark"]
        if not inv["is_red"]:
            if rem["pure_date"]:
                conn.execute(
                    "INSERT INTO collection (invoice_no, amount, receipt_date, source, import_batch_id, src_sheet, src_row) VALUES (?,?,?,?,?,?,?)",
                    (no, inv["total_amount"], rem["pure_date"], "import", batch_id, sheet, row),
                )
            else:
                for ym, amt in rem["receipts"]:
                    if amt == 0:
                        amt = inv["total_amount"]
                    conn.execute(
                        "INSERT INTO collection (invoice_no, amount, receipt_date, source, import_batch_id, src_sheet, src_row) VALUES (?,?,?,?,?,?,?)",
                        (no, amt, ym, "import", batch_id, sheet, row),
                    )
    # 方案E：落/更新收款认定快照，保证与本次写入一致
    _upsert_received_snapshot(conn, inv, batch_id)


def merge_collection_for_invoice(conn, invoice_no: str, batch_id: int,
                                 target: List[Dict]) -> None:
    """方案1(逐行手动修正)：以 rowid 为稳定主键做行级合并，绝不整票 DELETE。

    target 每项：{"rowid": int|None, "receipt_date": str, "amount": float, "person_name": str}
    - rowid=None  → 新增行
    - 有 rowid    → 编辑既有行（仅值变化才 UPDATE）
    - 既有行未出现在 target 中 → 删除
    写入范围限定在 invoice_no + source='import'，不影响其它发票，更不会误删后续月份收款。
    """
    existing = {
        r["id"]: r
        for r in conn.execute(
            "SELECT id, receipt_date, amount, person_name FROM collection "
            "WHERE invoice_no=? AND source='import'",
            (invoice_no,),
        )
    }
    target_ids = {t.get("id") for t in target if t.get("id")}
    # 删除：既有但不在目标集合
    for rid in existing:
        if rid not in target_ids:
            conn.execute("DELETE FROM collection WHERE id=?", (rid,))
    # 更新 / 插入
    for t in target:
        rid = t.get("id")
        date = (t.get("receipt_date") or "")[:10]
        amt = float(t.get("amount") or 0.0)
        person = (t.get("person_name") or "").strip()
        if rid is None:
            conn.execute(
                "INSERT INTO collection "
                "(invoice_no, amount, receipt_date, person_name, source, import_batch_id, src_sheet, src_row) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (invoice_no, amt, date, person, "import", batch_id, "manual", 0),
            )
        else:
            conn.execute(
                "UPDATE collection SET receipt_date=?, amount=?, person_name=? WHERE id=?",
                (date, amt, person, rid),
            )


def _refresh_snapshot_actual(conn, invoice_no: str, batch_id: int,
                             expected_json: str | None = None) -> None:
    """重算 received_snapshot.actual（保留/补写 expected），使校验与手动修正后一致。

    expected_json 不传则尝试沿用已有快照的 expected；都没有则兜底为空期望。
    """
    if expected_json is None:
        row = conn.execute(
            "SELECT expected_json FROM received_snapshot "
            "WHERE import_batch_id=? AND invoice_no=?",
            (batch_id, invoice_no),
        ).fetchone()
        expected_json = row["expected_json"] if row else \
            json.dumps({"total": 0.0, "items": []}, ensure_ascii=False)
    act_rows = conn.execute(
        "SELECT receipt_date, amount, person_name FROM collection "
        "WHERE invoice_no=? AND source='import'",
        (invoice_no,),
    ).fetchall()
    act_items = [
        {"ym": (r["receipt_date"] or "")[:7], "amount": r["amount"],
         "person": r["person_name"] or ""}
        for r in act_rows
    ]
    act_total = sum(it["amount"] for it in act_items)
    conn.execute(
        "INSERT OR REPLACE INTO received_snapshot "
        "(import_batch_id, invoice_no, expected_json, actual_json) VALUES (?,?,?,?)",
        (batch_id, invoice_no, expected_json,
         json.dumps({"total": act_total, "items": act_items}, ensure_ascii=False)),
    )


def _regen_charge_detail(conn, inv: Dict, batch_id: int) -> None:
    """按源重算某发票的经办人分摊（billing_amount），并清理源中已无的 import 行。

    供「导入校验-一键修正（经办人分摊）」复用；不动 received_override。
    """
    from app.engine.backfill import norm_type, staff_type_of
    no = inv["invoice_no"]
    sheet = inv.get("sheet_name") or ""
    row = inv.get("row_no") or 0
    parsed = {n: a for n, a in inv["handlers"]}
    for name, amount in inv["handlers"]:
        r = conn.execute(
            "SELECT id FROM charge_detail WHERE invoice_no=? AND person_name=?", (no, name)
        ).fetchone()
        if r:
            conn.execute(
                "UPDATE charge_detail SET billing_amount=?, person_type=?, src_sheet=?, src_row=? WHERE id=?",
                (amount, norm_type(staff_type_of(conn, name)), sheet, row, r["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source, "
                "import_batch_id, person_type, src_sheet, src_row, received_override) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (no, name, amount, "import", batch_id,
                 norm_type(staff_type_of(conn, name)), sheet, row, None),
            )
    for r in conn.execute(
        "SELECT id, person_name FROM charge_detail WHERE invoice_no=? AND source='import'", (no,)
    ):
        if r["person_name"] not in parsed:
            conn.execute("DELETE FROM charge_detail WHERE id=?", (r["id"],))


def _inherit_existing_handlers(conn, inv: Dict) -> None:
    """累计台账里重复出现的发票，若本次源行未写经办人金额（仅人名），
    且库内已有该发票的具体分摊，则沿用上次金额，避免被「平均分配」。

    - 新发票（库内无 charge_detail）：保持当前解析（多人无金额则均分）。
    - 源行含金额：以本次为准，不继承。
    - 源行纯人名 + 库内已有同集合具体分摊：用历史金额替换均分金额，
      并回写 handler_text，使台账镜像与归一化表一致、编辑时不再被均分覆盖。
    """
    handlers = inv.get("handlers") or []
    if not handlers:
        return
    src = (inv.get("handler_text") or "").strip()
    # 源经办人列已写金额 -> 以本次为准
    if any(ch.isdigit() for ch in src):
        return
    # 库内已有具体分摊？
    rows = conn.execute(
        "SELECT person_name, billing_amount FROM charge_detail "
        "WHERE invoice_no=? AND source='import'",
        (inv["invoice_no"],),
    ).fetchall()
    if not rows:
        return
    existing = {r["person_name"]: r["billing_amount"] for r in rows}
    new_names = [n for n, _ in handlers]
    # 经办人集合不一致 -> 不盲目继承，避免串数据
    if set(new_names) != set(existing.keys()):
        return
    # 用历史金额替换均分金额
    inv["handlers"] = [(n, existing.get(n, a)) for n, a in handlers]
    inv["handler_text"] = "、".join(
        f"{n}{int(a) if a == int(a) else a}" for n, a in inv["handlers"]
    )


def _insert_raw_ledger(conn, item: dict, batch_id: int, kind: str) -> None:
    """把发票台账解析结果逐行 1:1 镜像进 raw_ledger（synced=1）。"""
    seq = _raw_cell(item.get("raw_row"), item.get("header"), "序号")
    if kind == "invoice":
        date_raw = _raw_cell(item.get("raw_row"), item.get("header"), "开票日期", "开具日期")
        recv_raw = ""
        amt = item.get("total_amount") or 0.0
        amt_raw = _raw_cell(item.get("raw_row"), item.get("header"), "金额", "开票金额")
        handler = item.get("handler_text") or ""
        remark_raw = item.get("remark_raw")
        remark = remark_raw if remark_raw is not None else (item.get("remark") or "")
    else:  # prepayment (sheet4)
        date_raw = ""
        recv_raw = _raw_cell(item.get("raw_row"), item.get("header"), "收到日期")
        amt = item.get("amount") or 0.0
        amt_raw = _raw_cell(item.get("raw_row"), item.get("header"), "金额")
        handler = item.get("person_text") or ""
        remark = item.get("remark") or ""
    conn.execute(
        """INSERT INTO raw_ledger
           (sheet_key, sheet_name, row_no, seq, invoice_date_raw, invoice_no, buyer,
            amount_raw, amount_num, handler_text, remark, case_no, recv_date_raw, kind, synced, import_batch_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
        (item.get("sheet") or "", item.get("sheet_name") or "", item.get("row_no") or 0,
         seq, date_raw, item.get("invoice_no") or "", item.get("buyer") or "",
         amt_raw, amt, handler, remark, item.get("case_no") or "",
         recv_raw, kind, batch_id),
    )


# ---------------------------------------------------------------------------
# 销项文档
# ---------------------------------------------------------------------------
def _auto_snapshot() -> None:
    """导入前自动快照（防呆）"""
    try:
        from app.system.snapshot import save_snapshot
        save_snapshot("导入前自动快照", "自动", auto=True)
    except Exception:  # noqa: BLE001 快照失败不阻塞导入
        pass


def import_invoice_file(path: str, period: str) -> Dict:
    _auto_snapshot()
    invoices = parse_invoice_file(path, period)
    raw_rows, raw_warnings = parse_invoice_workbook(path, period)
    conn = get_conn()
    try:
        # 清旧批次的 raw_invoice 镜像（在 rollback 之前，否则 active 标记已变）
        old = conn.execute(
            "SELECT id FROM import_batch WHERE batch_type='invoice' AND period=? AND status='active'",
            (period,),
        ).fetchall()
        for r in old:
            conn.execute("DELETE FROM raw_invoice WHERE import_batch_id=?", (r["id"],))
        _drop_active_batch(conn, "invoice", period)
        archive = _archive_file(path, "invoice", period)
        batch_id = _new_batch(conn, "invoice", period, Path(path).name, archive, "")
        for inv in invoices:
            conn.execute(
                """INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, kind, status,
                   voucher_no, goods, net_amount, tax_rate, tax, orig_invoice_no, remark, source, import_batch_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (inv["invoice_no"], inv["invoice_date"], inv["buyer"], inv["total_amount"],
                 inv["kind"], inv["status"], inv["voucher_no"], inv["goods"],
                 inv["net_amount"], inv["tax_rate"], inv["tax"],
                 inv["orig_invoice_no"], inv["remark"], "import", batch_id),
            )
        # 原始镜表双写：全部 sheet 逐行 1:1 镜像（synced=1 表示与导入一致）
        for raw in raw_rows:
            conn.execute(
                """INSERT INTO raw_invoice
                   (sheet_name, row_no, seq, invoice_no, kind, invoice_date_raw, status, voucher_no,
                    buyer, total_amount_raw, net_amount_raw, tax_rate_raw, tax_raw, goods, remark, synced, import_batch_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (raw["sheet_name"], raw["row_no"], raw["seq"], raw["invoice_no"], raw["kind"],
                 raw["invoice_date_raw"], raw["status"], raw["voucher_no"], raw["buyer"],
                 raw["total_amount_raw"], raw["net_amount_raw"], raw["tax_rate_raw"], raw["tax_raw"],
                 raw["goods"], raw["remark"], batch_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"type": "invoice", "period": period, "count": len(invoices),
            "raw_count": len(raw_rows), "warnings": raw_warnings}


# ---------------------------------------------------------------------------
# 发票台账
# ---------------------------------------------------------------------------
def _validate_handler_names(conn, names: List[str], context: str) -> List[str]:
    """校验经办人是否在花名册，返回未匹配名单"""
    staff = _staff_names(conn)
    missing = []
    for n in names:
        if n and n not in staff and n not in HANDLER_WHITELIST:
            missing.append(n)
    return missing


def _apply_resolved(data: Dict, resolved: List[Dict]) -> None:
    """把问题行修正结果合并回解析数据：fix 行转正常结构追加，skip 行忽略"""
    for item in resolved:
        if item["action"] != "fix":
            continue
        p = data["problems"][item["index"]]
        d = item["data"]
        if d["kind"] == "invoice":
            data["invoices"].append({
                # 保留源文件原始 sheet 分类（如 sheet3=应收账款），不覆盖为 problem_fix
                "sheet": p.get("sheet") or "problem_fix",
                "sheet_name": SHEET_LABELS.get(p.get("sheet"), p.get("sheet") or "问题行修正"),
                "row_no": p["row_no"],
                # 透传原始台账行：修正后的行在预览里也能「查看原始台账行」
                "header": p.get("header") or [],
                "raw_row": p.get("raw_row") or [],
                "invoice_no": d["invoice_no"] or p.get("invoice_no", ""),
                "invoice_date": d["invoice_date"] or "",
                "buyer": p.get("buyer", ""),
                "total_amount": d["total_amount"],
                "handlers": d["handlers"],
                "handler_text": d["handler_text"],
                "remark_raw": p.get("remark_raw", ""),
                "remark": {"receipts": [], "remaining": None, "pure_date": None,
                           "is_red_remark": False, "is_red_off": False},
                "case_no": p.get("case_no", ""),
                "is_red": d["total_amount"] < 0,
                "split_receipts": d["split_receipts"],
            })
        else:  # prepayment
            data["prepayments"].append({
                "sheet": "sheet4",
                "sheet_name": "已入账未开票",
                "row_no": p["row_no"],
                "header": p.get("header") or [],
                "raw_row": p.get("raw_row") or [],
                "received_date": d["received_date"] or None,
                "buyer": p.get("buyer", ""),
                "amount": d["amount"],
                "person_text": d["person_text"],
                "remark": "",
                "case_no": "",
            })

    # 修正行此前不计入 sheet_totals，会导致「台账 sheet1+sheet2 合计 vs 销项合计」
    # 校验漏算修正行而误判失败。这里按 sheet 重新归集全部发票（含修正行）并重算。
    totals: dict = {}
    for inv in data.get("invoices", []):
        key = inv.get("sheet") or ""
        if key:
            totals[key] = totals.get(key, 0.0) + (inv.get("total_amount") or 0.0)
    if totals:
        data.setdefault("sheet_totals", {}).update(totals)
        data["sheet12_total"] = (
            data["sheet_totals"].get("sheet1", 0.0)
            + data["sheet_totals"].get("sheet2", 0.0)
        )


def validate_ledger_before_write(data: Dict, period: str) -> str | None:
    """发票台账写库前校验：返回 None 通过，返回文案为失败原因（不抛异常）。

    与写库前的两道校验完全一致（合计勾稽 + 经办人花名册），抽成函数是为了让
    统一确认对话框能在「确认入库」时就地校验——校验不过留在对话框里继续改，
    而不是改完一堆才报错、修改全丢。
    """
    conn = get_conn()
    try:
        # ---- 校验 1：sheet1+sheet2 合计 = 销项合计（本月销项须已导入）----
        # strftime 规范化匹配：兼容历史非零填充日期（2024-9-15 也算入 2024-09）
        inv_total = conn.execute(
            "SELECT COALESCE(SUM(total_amount),0) FROM invoice WHERE strftime('%Y-%m', invoice_date) = ?",
            (period,),
        ).fetchone()[0]
        s12 = data.get("sheet12_total", 0.0)
        if abs(inv_total - s12) > 0.01:
            # 允许销项未导入的情况单独提示
            if not (inv_total == 0 and "sheet1" not in (data.get("sheet_totals") or {})):
                return (
                    f"校验失败: 台账 sheet1+sheet2 合计({s12:g}) ≠ 销项文档本月价税合计({inv_total:g})。"
                    f"请确认已先导入 {period} 销项文档；如已修正过问题行，请核对修正金额。"
                )

        # ---- 校验 2：经办人必须在花名册 ----
        all_handlers: List[str] = []
        for inv in data.get("invoices", []):
            all_handlers += [h[0] for h in (inv.get("handlers") or [])]
        missing = _validate_handler_names(conn, all_handlers, "发票台账")
        if missing:
            return (
                "经办人不在职工花名册中（请先在员工管理中添加）: "
                + ", ".join(sorted(set(missing)))
            )
        return None
    finally:
        conn.close()


def import_ledger_file(path: str, period: str,
                       on_problems: callable | None = None,
                       on_preview: callable | None = None,
                       on_confirm: callable | None = None) -> Dict:
    """导入发票台账。

    两条路径二选一：
    1) on_confirm（新，统一确认）：(data, validator) -> data | None。
       问题行修正与写前预览合并在同一个对话框里；validator(data) -> str | None
       供对话框在「确认入库」时就地校验。返回 None 表示取消导入。
    2) on_problems + on_preview（旧，逐步）：先修问题行，再校验，最后预览确认。
       on_problems: (problems) -> resolved: list | None；
       on_preview: (data) -> data | None。
    """
    data = parse_ledger_file(path, period)
    if on_confirm is not None:
        confirmed = on_confirm(data, lambda d: validate_ledger_before_write(d, period))
        if confirmed is None:
            raise ImportError_("已取消导入")
        if isinstance(confirmed, dict):
            data = confirmed
    elif data["problems"] and on_problems is not None:
        resolved = on_problems(data["problems"])
        if resolved is None:
            raise ImportError_("已取消导入")
        _apply_resolved(data, resolved)
        data["problems"] = []
    if on_preview is not None and on_confirm is None:
        confirmed = on_preview(data)
        if confirmed is None:
            raise ImportError_("已取消导入")
        if isinstance(confirmed, dict):
            data = confirmed
    return commit_ledger_import(data, period, path)


def commit_ledger_import(data: Dict, period: str, path: str) -> Dict:
    """把已确认（含修正结果与已收覆盖值）的发票台账数据写入数据库。

    覆盖式导入：清同账期 active 批次 → raw_ledger 镜像双写 → invoice / charge_detail /
    prepayment upsert → import_batch。写前再做一次 validate_ledger_before_write 兜底。
    """
    _auto_snapshot()
    conn = get_conn()
    try:
        # ---- 校验兜底（确认对话框已就地校验过，这里再拦一次）----
        err = validate_ledger_before_write(data, period)
        if err:
            raise ImportError_(err)

        # ---- 覆盖式导入 ----
        # 清旧批次的 raw_ledger 镜像（在 rollback 之前，否则 active 标记已变）
        old = conn.execute(
            "SELECT id FROM import_batch WHERE batch_type='ledger' AND period=? AND status='active'",
            (period,),
        ).fetchall()
        for r in old:
            conn.execute("DELETE FROM raw_ledger WHERE import_batch_id=?", (r["id"],))
        _drop_active_batch(conn, "ledger", period)
        archive = _archive_file(path, "ledger", period)
        batch_id = _new_batch(conn, "ledger", period, Path(path).name, archive, "")

        for inv in data["invoices"]:
            no = inv["invoice_no"]
            # 累计台账重复行未写金额时，沿用库内已有具体分摊（防「平均分配」）
            _inherit_existing_handlers(conn, inv)
            # 原始镜表双写：发票台账逐行 1:1 镜像（synced=1 表示与导入一致）
            _insert_raw_ledger(conn, inv, batch_id, "invoice")
            # 发票 upsert：不存在则创建（期外发票），存在则补案号
            exist = conn.execute("SELECT case_no FROM invoice WHERE invoice_no=?", (no,)).fetchone()
            if exist:
                if inv.get("case_no") and not exist["case_no"]:
                    conn.execute("UPDATE invoice SET case_no=? WHERE invoice_no=?", (inv["case_no"], no))
            else:
                conn.execute(
                    """INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, case_no,
                       source, import_batch_id, orig_invoice_no, src_sheet, src_row)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (no, inv["invoice_date"], inv["buyer"], inv["total_amount"],
                     inv.get("case_no"), "import", batch_id, "",
                     inv.get("sheet_name") or "", inv.get("row_no") or 0),
                )
            # 经办人拆分（已存在则更新覆盖值，避免重导时丢失确认结果）
            # 双人名容错：按姓名聚合开票额，避免 charge_detail(发票号,经办人) 唯一约束冲突
            ov_map = inv.get("received_overrides") or {}
            _agg_h: dict = {}
            for _n, _a in inv["handlers"]:
                _agg_h[_n] = _agg_h.get(_n, 0.0) + _a
            for name, amount in _agg_h.items():
                r = conn.execute(
                    "SELECT id FROM charge_detail WHERE invoice_no=? AND person_name=?", (no, name)
                ).fetchone()
                ov = ov_map.get(name)  # None = 用系统推导（received_override 留空）
                if not r:
                    conn.execute(
                        "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source, import_batch_id, person_type, src_sheet, src_row, received_override) VALUES (?,?,?,?,?,?,?,?,?)",
                        (no, name, amount, "import", batch_id,
                         norm_type(staff_type_of(conn, name)),
                         inv.get("sheet_name") or "", inv.get("row_no") or 0, ov),
                    )
                else:
                    conn.execute(
                        "UPDATE charge_detail SET received_override=? WHERE id=?", (ov, r["id"])
                    )
            # 收款明细：先删该发票 import 旧记录（以最新台账为准），再插入
            _write_collection_for_invoice(conn, inv, batch_id)

        # 预收款（sheet4）
        for pp in data["prepayments"]:
            conn.execute(
                """INSERT INTO prepayment (received_date, buyer, amount, person_text, case_no, remark,
                   source, import_batch_id, src_sheet, src_row) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (pp["received_date"], pp["buyer"], pp["amount"], pp["person_text"],
                 pp["case_no"], pp["remark"], "import", batch_id,
                 pp.get("sheet_name") or "sheet4", pp.get("row_no") or 0),
            )
            # 原始镜表双写（kind=prepayment）
            _insert_raw_ledger(conn, pp, batch_id, "prepayment")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "type": "ledger",
        "period": period,
        "batch_id": batch_id,
        "invoice_count": len(data["invoices"]),
        "prepayment_count": len(data["prepayments"]),
        "sheet12_total": data["sheet12_total"],
    }


# ---------------------------------------------------------------------------
# 费用台账
# ---------------------------------------------------------------------------
def import_expense_file(path: str, period: str) -> Dict:
    _auto_snapshot()
    items = parse_expense_file(path, period)
    conn = get_conn()
    try:
        names = []
        for it in items:
            if it["actual_handler"]:
                names.append(it["actual_handler"])
            if it["handler"]:
                names.append(it["handler"])
        missing = _validate_handler_names(conn, names, "费用台账")
        if missing:
            raise ImportError_(
                f"经办人不在职工花名册中（请先在员工管理中添加）: {', '.join(sorted(set(missing)))}"
            )

        # 费用类型校验：不在维护名单（费用归类）中的类型 → 报错
        from app.engine.expense_cat import check_unknown
        etypes = [it["expense_type"] for it in items if it.get("expense_type")]
        unknown = check_unknown(conn, etypes)
        if unknown:
            raise ImportError_(
                f"费用类型不在维护名单中（请先到「数据维护 → 费用类型」添加或归类）: {', '.join(sorted(set(unknown)))}"
            )

        _drop_active_batch(conn, "expense", period)
        archive = _archive_file(path, "expense", period)
        batch_id = _new_batch(conn, "expense", period, Path(path).name, archive, "")
        for it in items:
            conn.execute(
                """INSERT INTO expense_ledger (period, seq, exp_date, name, ticket_no, handler, actual_handler,
                   expense_amount, tax_amount, book_amount, expense_type, voucher_no, subject1, subject2,
                   source, import_batch_id, person_type)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (it["period"], it["seq"], it["exp_date"], it["name"], it["ticket_no"],
                 it["handler"], it["actual_handler"], it["expense_amount"], it["tax_amount"],
                 it["book_amount"], it["expense_type"], it["voucher_no"], it["subject1"], it["subject2"],
                 "import", batch_id, norm_type(staff_type_of(conn, it["actual_handler"]))),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"type": "expense", "period": period, "count": len(items)}


def import_salary_file(path: str, period: str) -> Dict:
    """导入工资表（覆盖式：同账期重导会清掉旧批次的 raw_salary 镜像）。

    账期 period 由调用方从**文件名**解析（如 25.1 -> 2025-01）；
    表内中文年月一律不参与（各 sheet 期间互不相同且与文件名不一致）。
    """
    _auto_snapshot()
    data = parse_salary_file(path, period)
    if not data["items"]:
        raise ImportError_("未能从该文件解析出工资数据行，请确认选择的是工资表文件")
    conn = get_conn()
    try:
        old = conn.execute(
            "SELECT id FROM import_batch WHERE batch_type='salary' AND period=? AND status='active'",
            (period,),
        ).fetchall()
        for r in old:
            conn.execute("DELETE FROM raw_salary WHERE import_batch_id=?", (r["id"],))
        _drop_active_batch(conn, "salary", period)
        archive = _archive_file(path, "salary", period)
        batch_id = _new_batch(conn, "salary", period, Path(path).name, archive, "")
        for item in data["items"]:
            _insert_raw_salary(conn, item, batch_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"type": "salary", "period": period, "count": len(data["items"]),
            "sheet_count": data["sheet_count"]}


def _insert_raw_salary(conn, item: dict, batch_id: int) -> None:
    """把工资表解析结果逐行 1:1 镜像进 raw_salary（保留所有原始列、不归一化）。"""
    conn.execute(
        """INSERT INTO raw_salary
           (sheet_key, sheet_name, block_no, row_no, item_type, seq, staff_name,
            share_raw, share_num, salary_raw, salary_num, partner_raw, partner_num,
            tax_raw, tax_num, fund_raw, fund_num, net_raw, net_num,
            pension_raw, medical_raw, unemployment_raw, remark, import_batch_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item.get("sheet_key") or "", item.get("sheet_name") or "",
         item.get("block_no") or 1, item.get("row_no") or 0,
         item.get("item_type") or "", item.get("seq") or "",
         item.get("staff_name") or "",
         item.get("share_raw") or "", item.get("share_num") or 0.0,
         item.get("salary_raw") or "", item.get("salary_num") or 0.0,
         item.get("partner_raw") or "", item.get("partner_num") or 0.0,
         item.get("tax_raw") or "", item.get("tax_num") or 0.0,
         item.get("fund_raw") or "", item.get("fund_num") or 0.0,
         item.get("net_raw") or "", item.get("net_num") or 0.0,
         item.get("pension_raw") or "", item.get("medical_raw") or "",
         item.get("unemployment_raw") or "", item.get("remark") or "",
         batch_id),
    )
