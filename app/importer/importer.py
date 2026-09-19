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
from app.engine.backfill import (
    HANDLER_WHITELIST, all_staff_names, library_invoice_nos, missing_handlers, norm_type,
    staff_type_of,
)
from app.engine.collection import over_collection_message
from app.engine.raw_ledger import SHEET_LABELS
from app.importer.excel_reader import col_index
from app.importer.expense_import import parse_expense_file
from app.importer.invoice_import import parse_invoice_file, parse_invoice_workbook
from app.importer.ledger_import import (
    is_deferred_invoice, is_sheet3_row, parse_ledger_file, split_deferred,
)
from app.importer.salary_import import parse_salary_file
from app.importer.excel_reader import ImportError_

ARCHIVE_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "archive"

# 非员工经办人白名单（公共费用等）已移至 app.engine.backfill，与补录校验共用同一份。
# 此处保留模块级别名（历史引用兼容）。


def _staff_names(conn) -> set:
    """花名册全部姓名（**不过滤 is_active**，「停用」已取消）。"""
    return all_staff_names(conn)


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
    # 批 3-1 / 3-2b / 3-2c：本批的三类「导入时留痕」随之失效（只清本模块自己的三个 dim，
    # 旧「导入后」页写的人工备注 'merged'/'handler'/'received' **不动**）。
    # 三类同生共死：撤销台账批次后，复核页 post 模式既看不到「已确认」，也不该再看到
    # 「导入时已修改」「已补录」—— 那些都是这一次导入的产物。留痕按账期存放，故先取账期。
    _b = conn.execute("SELECT batch_type, period FROM import_batch WHERE id=?",
                      (batch_id,)).fetchone()
    if _b and _b[0] == "ledger" and _b[1]:
        from app.engine.import_confirm import (
            BACKFILL_DIM, CONFIRM_DIM, EDIT_DIM,
        )
        conn.execute(
            "DELETE FROM anomaly_note WHERE period=? AND dim IN (?,?,?)",
            (_b[1], CONFIRM_DIM, EDIT_DIM, BACKFILL_DIM))
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
    """从原始行按表头取某列文本（兼容多别名）。

    **与解析侧同一口径**：直接复用 `excel_reader.col_index` 定位列（列名去空白后
    「包含」任一别名的最左列），与 `ledger_import._parse_invoice_sheet` /
    `_parse_sheet4` 的取列方式完全一致 —— 保证镜像值与解析值取自**同一列**，
    导入后复核页「源 ⇄ 库」可逐字对照。

    两个历史坑（2026-09-17）：
    1. **表头带排版空格**：真台账 12 个文件的对方列表头都是「对  方」（中间两个
       空格），原先按原文精确比较 → 整列取不到 → raw_ledger.buyer 93/93 全空、
       「发票台账」页对方列空白（用户报障）。
    2. **别名用 `==` 而非包含**：预收款表买方列若叫「汇款单位」，别名「汇款」
       永远匹配不上，而解析侧（col_index 用包含）却认得 → 镜像与解析不一致。
       现统一为包含匹配，两处不会再分叉。
    """
    if not header or not row:
        return ""
    i = col_index(header, *names)
    if i < 0 or i >= len(row):
        return ""
    return (row[i] or "").strip()


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
    - 源行纯人名 + 库内已有同集合具体分摊：用历史金额替换均分金额（供 charge_detail），
      并回写 handler_text 保持一致；台账镜表本身固定写台账原文（见 _insert_raw_ledger），
      纯人名与库内具体分摊的差异由复核页「纯人名均分沿用首月拆分」豁免兜住。
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
    """把发票台账解析结果逐行 1:1 镜像进 raw_ledger（synced=1）。

    镜像的文本字段（经办人/对方/案号）一律取【原始台账行】（raw_row + header），
    而不是解析或人工修改后的值：否则导入前复核页手工补录的经办人（源台账本就为空）
    会被写进镜像，使导入后复核页「源」侧显示手工值而非台账原文，源/库无法对照。
    仅当拿不到原始行（无 header/raw_row 的程序化构造行）才回退用解析值。
    发票号码仍取解析值——问题行常需纠正号码，镜像必须存纠正后的号才能与库对齐。
    """
    seq = _raw_cell(item.get("raw_row"), item.get("header"), "序号")
    has_raw = bool(item.get("header")) and bool(item.get("raw_row"))
    if kind == "invoice":
        date_raw = _raw_cell(item.get("raw_row"), item.get("header"), "开票日期", "开具日期")
        recv_raw = ""
        amt = item.get("total_amount") or 0.0
        amt_raw = _raw_cell(item.get("raw_row"), item.get("header"), "金额", "开票金额")
        if has_raw:
            handler = _raw_cell(item.get("raw_row"), item.get("header"), "经办人")
            buyer = _raw_cell(item.get("raw_row"), item.get("header"), "对方", "购方")
            case_no = _raw_cell(item.get("raw_row"), item.get("header"), "案号")
        else:
            handler = item.get("handler_text") or ""
            buyer = item.get("buyer") or ""
            case_no = item.get("case_no") or ""
        remark_raw = item.get("remark_raw")
        remark = remark_raw if remark_raw is not None else (item.get("remark") or "")
    else:  # prepayment (sheet4)
        date_raw = ""
        recv_raw = _raw_cell(item.get("raw_row"), item.get("header"), "收到日期")
        amt = item.get("amount") or 0.0
        amt_raw = _raw_cell(item.get("raw_row"), item.get("header"), "金额")
        if has_raw:
            handler = _raw_cell(item.get("raw_row"), item.get("header"), "经办人")
            # 别名与 ledger_import._parse_sheet4 的 col_index(header,"对方","购方","汇款")
            # 保持一致：预收款表的买方列可能叫「汇款单位」，少这个别名镜像会取空。
            buyer = _raw_cell(item.get("raw_row"), item.get("header"), "对方", "购方", "汇款")
            case_no = _raw_cell(item.get("raw_row"), item.get("header"), "案号")
        else:
            handler = item.get("person_text") or ""
            buyer = item.get("buyer") or ""
            case_no = item.get("case_no") or ""
        remark = item.get("remark") or ""
    conn.execute(
        """INSERT INTO raw_ledger
           (sheet_key, sheet_name, row_no, seq, invoice_date_raw, invoice_no, buyer,
            amount_raw, amount_num, handler_text, remark, case_no, recv_date_raw, kind, synced, import_batch_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
        (item.get("sheet") or "", item.get("sheet_name") or "", item.get("row_no") or 0,
         seq, date_raw, item.get("invoice_no") or "", buyer,
         amt_raw, amt, handler, remark, case_no,
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
    """校验经办人是否在花名册（或白名单），返回未匹配名单（去重 + 升序）。

    与补录保存校验共用 `backfill.missing_handlers`，保证两处口径完全一致。
    """
    return missing_handlers(conn, names)


def _apply_resolved(data: Dict, resolved: List[Dict], period: str,
                    in_library: set | None = None) -> None:
    """把问题行修正结果合并回解析数据：fix 行转正常结构追加，skip 行忽略

    修正行若落在 sheet3（应收账款），则同样转入 deferred（补录原票 / 已入库确认收款），
    与解析期口径一致——避免「修正后反而被自动建票」的不一致。
    in_library：库中已有票号集合（决定 deferred 行的 need_backfill 标记）；
    不传则现查 invoice 表。
    """
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
    # 校验漏算修正行而误判失败。这里按 sheet 重新归集全部发票（含修正行与已转入
    # deferred 的期外行，保持源口径）并重算。
    totals: dict = {}
    for inv in list(data.get("invoices", [])) + list(data.get("deferred", [])):
        key = inv.get("sheet") or ""
        if key:
            totals[key] = totals.get(key, 0.0) + (inv.get("total_amount") or 0.0)
    if totals:
        data.setdefault("sheet_totals", {}).update(totals)
        data["sheet12_total"] = (
            data["sheet_totals"].get("sheet1", 0.0)
            + data["sheet_totals"].get("sheet2", 0.0)
        )

    # 修正出来的 sheet3 行同样转 deferred（与解析期口径一致；票号判定，见 split_deferred）
    split_deferred(data, period, in_library)


def _append_receipts_to_existing(conn, invoice_no: str, receipts: List[Tuple[str, float, str]],
                                 batch_id: int, sheet: str = "", row: int = 0) -> str | None:
    """**已入库票追加本次收款** —— A10（复核页确认收款）与补录 create=False 共用同一通道。

    口径（2026-09-17 用户拍板）：
    - 台账收款一律**追加**，绝不删该票已有收款（历史 import 批与 manual 补录都不受影响）；
    - 幂等：先按 import_batch_id 清本批自己的行，再逐笔 INSERT；
    - 累计收款（**全来源** import + manual）> 开票总额 → 视为**台账信息错误**：
      **不写入**并返回可读报错文案（调用方记入 over_collected，不中止整批）。

    receipts：[("经办人", 金额, "YYYY-MM-DD"), ...]
    返回 None = 已写入；返回文案 = 未写入 + 原因。
    """
    incoming = sum(a for _n, a, _d in receipts)
    msg = over_collection_message(conn, invoice_no, incoming, exclude_batch_id=batch_id)
    if msg:
        return msg
    conn.execute(
        "DELETE FROM collection WHERE invoice_no=? AND source='import' AND import_batch_id=?",
        (invoice_no, batch_id),
    )
    for name, amt, date in receipts:
        conn.execute(
            "INSERT INTO collection (invoice_no, amount, receipt_date, person_name, "
            "source, import_batch_id, src_sheet, src_row) VALUES (?,?,?,?,?,?,?,?)",
            (invoice_no, amt, date, name, "import", batch_id, sheet, row),
        )
    return None


def _write_backfills(conn, backfills: List[Dict], batch_id: int) -> List[Dict]:
    """把复核页收集的补录条目写进**当前事务**（B2b）。

    返回「未能写入」条目（可诊断）；调用方据此决定是否中止整批。
    统一结构（B2c）：每项含 `invoice_no` + `create: bool`；`create=True` = 新票补录
    （invoice(source='manual') + charge_detail + collection），`create=False` = 票已在库、
    只追加收款（走 `_append_receipts_to_existing`，与 A10 同一实现）。

    ⚠️ 必须放在普通发票循环**之后**：同账期 sheet1/2 已按 import 建票时，
    `apply_backfill` 的非 manual 守卫会拦下（否则会把销项票静默改成 manual）。
    """
    from app.engine.backfill_module import apply_backfill, build_backfill
    problems: List[Dict] = []
    for bf in backfills or []:
        no = (bf.get("invoice_no") or "").strip()
        if not bf.get("create", True):
            receipts = [
                ((n or "").strip(), float(a or 0.0), (d or "")[:10])
                for n, a, d in (bf.get("collections") or [])
                if (n or "").strip() and float(a or 0.0) > 0.001
            ]
            if not no or not receipts:
                continue
            if conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (no,)).fetchone() is None:
                problems.append({"invoice_no": no, "reason": "票不在库，无法只更新收款"})
                continue
            msg = _append_receipts_to_existing(conn, no, receipts, batch_id)
            if msg:
                problems.append({"invoice_no": no, "reason": msg})
            continue
        # create=True：校验 → 写入（build 在 accept() 与 validate 已跑过，这里兜第三次）
        payload = build_backfill(conn, bf)
        apply_backfill(conn, payload)
    return problems


def _validate_backfills(conn, data: Dict, in_library: set | None = None) -> str | None:
    """补录条目写前校验（B2d 第二次校验 / B2e 重复票号）。通过返回 None。

    与 `build_backfill` **同源**（票号/日期必填、至少一名经办人、须在花名册、已收不超合计），
    另加三类「同批冲突」诊断 —— 一律中止写库并指出具体票号，绝不静默去重：
    ① 同批两个补录同票号（§11 B2e 的真实现场：同账期 sheet3 两行同号、
       同账期两张红字引用同一原票）；
    ② 补录票号**已在库**（A5 刷新后应已转「已入库」，走到这里说明状态滞后）；
    ③ 补录票号与**本批 sheet1/2** 同号（该票本次随台账入库，无需补录）。
    """
    from app.engine.backfill_module import build_backfill
    backfills = data.get("backfills") or []
    if not backfills:
        return None
    seen: Dict[str, int] = {}
    created: Dict[str, int] = {}   # 只含 create=True 的（要建票的）
    for k, bf in enumerate(backfills, 1):
        no = (bf.get("invoice_no") or "").strip()
        if bf.get("create", True):
            try:
                build_backfill(conn, bf)
            except ValueError as e:
                return f"补录发票 {no or '（未填号码）'}：{e}"
            created[no] = k
        else:
            # create=False：票已在库、只追加收款（无 handlers，不跑 build_backfill）
            if not no:
                return f"补录条目（第 {k} 处）未填写发票号码。"
            receipts = [
                (n or "").strip() for n, a, _d in (bf.get("collections") or [])
                if (n or "").strip() and float(a or 0.0) > 0.001
            ]
            if not receipts:
                return f"补录发票 {no}（第 {k} 处）没有可写入的收款，请核对后重试。"
        if no in seen:
            return (f"补录票号 {no} 在本次导入中出现 {seen[no] + 1} 次"
                    f"（第 {seen[no]} 处、第 {k} 处），请只保留一处补录后重试。")
        seen[no] = k
    if in_library is None:
        in_library = library_invoice_nos(conn)
    for no in created:
        if no in in_library:
            return f"补录票号 {no} 已在库中，无需补录。请刷新复核页后重试。"
    batch_nos = {
        (inv.get("invoice_no") or "").strip()
        for inv in (data.get("invoices") or []) if not is_sheet3_row(inv)
    } - {""}
    for no in created:
        if no in batch_nos:
            return (f"补录票号 {no} 与本次台账 sheet1/2 中的发票同号，"
                    "该票本次会随台账入库，无需补录。请从补录中移除该票后重试。")
    return None


def validate_ledger_before_write(data: Dict, period: str,
                                 in_library: set | None = None) -> str | None:
    """发票台账写库前校验：返回 None 通过，返回文案为失败原因（不抛异常）。

    与写库前的两道校验完全一致（合计勾稽 + 经办人花名册），抽成函数是为了让
    统一确认对话框能在「确认入库」时就地校验——校验不过留在对话框里继续改，
    而不是改完一堆才报错、修改全丢。
    in_library：库中已有票号集合（转给 `is_deferred_invoice`）；不传则现查 invoice 表。
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
        # sheet3（应收账款）行本次一律不落库（见 split_deferred，D1 甲），其经办人不在
        # 本次校验范围内：需补录的票由 save_backfill 用同一份 missing_handlers 再校验一次；
        # 已在库的票压根不建票、不分摊。
        # `is_sheet3_row` 短路后 `is_deferred_invoice` 不会触发查库（sheet3 已跳过）。
        all_handlers: List[str] = []
        for inv in data.get("invoices", []):
            if is_sheet3_row(inv) or is_deferred_invoice(inv, period, in_library):
                continue
            all_handlers += [h[0] for h in (inv.get("handlers") or [])]
        missing = _validate_handler_names(conn, all_handlers, "发票台账")
        if missing:
            return (
                "经办人不在职工花名册中（请先在员工管理中添加）: "
                + ", ".join(sorted(set(missing)))
            )

        # ---- 校验 3：复核页收集的补录票（data["backfills"]，阶段 3 B2d/B2e）----
        return _validate_backfills(conn, data, in_library)
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

    「库中已有票号集合」在入口读一次并贯穿全程（解析切分 / 校验 / 提交），
    保证同一次导入内 need_backfill 判定不漂移（写入发生在本函数末尾之后）。
    """
    lib = library_invoice_nos()
    data = parse_ledger_file(path, period, lib)
    if on_confirm is not None:
        confirmed = on_confirm(data, lambda d: validate_ledger_before_write(d, period, lib))
        if confirmed is None:
            raise ImportError_("已取消导入")
        if isinstance(confirmed, dict):
            data = confirmed
    elif data["problems"] and on_problems is not None:
        resolved = on_problems(data["problems"])
        if resolved is None:
            raise ImportError_("已取消导入")
        _apply_resolved(data, resolved, period, lib)
        data["problems"] = []
    if on_preview is not None and on_confirm is None:
        confirmed = on_preview(data)
        if confirmed is None:
            raise ImportError_("已取消导入")
        if isinstance(confirmed, dict):
            data = confirmed
    # 兜底：任何路径（含统一确认对话框内就地修正）最终都再切一次 sheet3（幂等）。
    split_deferred(data, period, lib)
    return commit_ledger_import(data, period, path, in_library=lib)


def commit_ledger_import(data: Dict, period: str, path: str,
                         in_library: set | None = None,
                         backfills: List[Dict] | None = None) -> Dict:
    """把已确认（含修正结果与已收覆盖值）的发票台账数据写入数据库。

    覆盖式导入：清同账期 active 批次 → raw_ledger 镜像双写 → invoice / charge_detail /
    prepayment upsert → import_batch。写前再做一次 validate_ledger_before_write 兜底。

    sheet3（应收账款）**全部行**：**只写 raw_ledger 镜像**，不建 invoice
    / charge_detail / collection —— 其中「需补录原票」的行是历史应收，原票并未随文档
    入库，须由「补录原票」逐张确认后写入；「已在库」的行只把台账收款带出供复核页确认
    （D1 甲：绝不能走普通票路径，否则 _write_collection_for_invoice 的 DELETE 会抹掉
    该票历史收款）。见 app/engine/raw_ledger.deferred_sheet3_invoices。
    入口处先切分一次（幂等），保证复核页直连 commit 的路径也走同一口径。

    in_library：库中已有票号集合；不传则现查 invoice 表。

    backfills（阶段 3 B2b）：复核页收集的补录票，**在同一事务内**写入
    `invoice(source='manual') + charge_detail + collection`；任一步失败 → 整批回滚
    （台账与补录同进同出）。不传时回落到 `data["backfills"]`。
    补录票**不写 received_snapshot**、`import_batch_id` **留空** —— 它不属任何导入批次，
    撤销该批台账不应连带删除补录（见 `apply_backfill`）。

    写入顺序（阶段 4-1）：sheet3 镜表 → 普通票 → **补录票** → **A10 确认收款**。
    A10 必须排在补录**之后**（补录票此刻才在 invoice 表，否则会被「不在库就跳过」误伤），
    且**跳过本批补录覆盖的票号**（补录是显式指令，与 A10 同时命中的话同一笔收款会
    按 manual + import 各记一份 → 重复计 → 被超额校验判成台账错误）。
    """
    if in_library is None:
        in_library = library_invoice_nos()
    if backfills is None:
        backfills = data.get("backfills") or []
    split_deferred(data, period, in_library)
    _auto_snapshot()
    conn = get_conn()
    try:
        # ---- 校验兜底（确认对话框已就地校验过，这里再拦一次）----
        err = validate_ledger_before_write(data, period, in_library)
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

        # ---- sheet3（应收账款）：只写镜表，不落 invoice/charge_detail/collection ----
        # 镜表仍 1:1 保留原始行（含账期），既供「发票台账」页查看，也是
        # 「待补录（源 B）」的派生依据。行上的 need_backfill 区分「需补录原票」/
        # 「已入库，请确认收款」——后者只把台账收款带出，等复核页确认后才动 collection。
        for inv in data.get("deferred", []) or []:
            _insert_raw_ledger(conn, inv, batch_id, "invoice")

        over_collected: List[Dict] = []

        for inv in data["invoices"]:
            no = inv["invoice_no"]
            # D1 甲兜底：sheet3 行绝不走普通票路径（否则下面
            # _write_collection_for_invoice 的 DELETE 会抹掉该票历史收款）。
            # 正常路径下 split_deferred 已保证 invoices 不含 sheet3 行，此处再拦一层；
            # 该行仍写镜表，保证 raw_ledger 与台账文件 1:1。
            if is_sheet3_row(inv):
                _insert_raw_ledger(conn, inv, batch_id, "invoice")
                continue
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

        # ---- B2b：补录票随台账**同一事务**入库（阶段 3）----
        # 放在普通发票循环**之后**：本批 sheet1/2 若已按 import 建同号票，
        # apply_backfill 的非 manual 守卫会拦下（绝不把销项票静默改成 manual）。
        # 任一条目写失败 → 异常冒泡 → 整个事务回滚（台账与补录同进同出，B2d）。
        bf_problems = _write_backfills(conn, backfills, batch_id)
        for p in bf_problems:
            over_collected.append({"invoice_no": p["invoice_no"], "period": period,
                                   "message": p["reason"]})

        # ---- A10：复核页**已确认收款**的「已在库」sheet3 票 → 追加该票收款 ----
        # 口径（2026-09-17 用户拍板）：
        #   ① 台账收款一律**追加**，绝不删该票已有收款 —— 历史批与补录(manual)都不受影响；
        #   ② 追加后累计收款（**全来源** import+manual）> 开票总额 → 视为**台账信息错误**：
        #      该行不写并记入 over_collected（"剩余应收 0 元，已收款完成"），
        #      不中止整批（该行作为问题行由复核页提示用户修正台账）；
        #   ③ 未确认（receipt_confirmed 非真）→ 完全不动。
        # 幂等：本批上次写的行由本批 import_batch_id 标记 —— 覆盖式导入时已被
        # rollback_batch 按 import_batch_id 清掉（rollback_batch:78），此处再按批删一次兜底。
        #
        # ⚠️ 位置（阶段 4-1）：必须放在 `_write_backfills` **之后**。
        #   阶段 3 时它在补录之前，下面「不在库就跳过」会把**本批刚补录出来的票**一起跳过
        #   （A10 跑时补录票尚未建）→ 该票的「确认收款」永远写不进去。
        #   挪到补录之后，补录票此刻已在 invoice 表，判定恢复正常。
        # ⚠️ 去重（阶段 4-1）：`backfills` 覆盖的票号一律跳过 —— 补录是**显式指令**
        #   （create=True 建票并写 manual 收款 / create=False 直接追加收款）；若同一票又被
        #   A10 按「台账确认收款」写一遍（source='import'），同一笔收款会 import+manual
        #   各记一份 → 全来源合计翻倍 → 被 ② 判成超额而整行拒写（假报错）。
        bf_nos = {
            (b.get("invoice_no") or "").strip() for b in (backfills or [])
        } - {""}
        for rel in data.get("deferred", []) or []:
            if not rel.get("receipt_confirmed"):
                continue
            no = (rel.get("invoice_no") or "").strip()
            receipts = [
                ((_n or "").strip(), float(_a or 0.0), (_d or "")[:10])
                for _n, _a, _d in (rel.get("split_receipts") or [])
                if (_n or "").strip() and float(_a or 0.0) > 0.001
            ]
            if not no or not receipts:
                continue
            if no in bf_nos:
                continue  # 本批补录已写该票收款（显式优先）→ 绝不重复追加
            if conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (no,)).fetchone() is None:
                continue  # 票不在库（本批也没补录它）→ 无票可挂，跳过
            msg = _append_receipts_to_existing(
                conn, no, receipts, batch_id,
                rel.get("sheet_name") or "", rel.get("row_no") or 0)
            if msg:
                over_collected.append({"invoice_no": no, "period": period, "message": msg})

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

        # ---- 导入时「确认」留痕（批 3-1）----
        # 为什么写：四态里「兜底低置信」「红字⇄蓝字不一致」两类疑问**只能靠人点确认消掉**，
        #   而确认只改复核页内存集合 → 库里零痕迹 → 批 1b 的「导入后」模式重推四态时必然
        #   把它们重新报成「待确认」，而 post 又隐藏了「确认」按钮 ⇒ 点不掉的假待办。
        # 落点：复用 `anomaly_note` 的独立 dim（零 schema），与旧「导入后」页写的人工备注
        #   （dim='merged'）分开存放，互不覆盖。
        # 事务：与台账**同一事务**；先清本账期本 dim —— 覆盖式重导同一账期时，
        #   上一次的确认已不适用，必须失效（否则会拿旧确认去压制新一批的疑问）。
        from app.engine.import_confirm import (
            clear_confirmations, save_confirmations,
            clear_edit_hints, save_edit_hints,
            clear_backfill_hints, save_backfill_hints,
        )
        clear_confirmations(conn, period)
        confirm_n = save_confirmations(conn, period, data.get("confirmations"))
        # 批 3-2b：导入时就地修改过的行（改了哪些字段）同事务落留痕（dim=import_edit）。
        # 为什么写：sheet3 在库行的文本字段（购方/经办人分摊/案号）库内零落点，而「导入后」
        #   模式从 raw_ledger（原文镜表）重建 → 会无声显示旧值；留痕让 post 页原因列
        #   标注「导入时已修改：字段…」，修改记录页（change_log）之外多一个就地可见的提示。
        # 事务：与确认留痕同款 —— 同一事务；先清本账期本 dim，覆盖式重导时旧提示整体失效。
        clear_edit_hints(conn, period)
        edit_hint_n = save_edit_hints(conn, period, data.get("edit_hints"))
        # 批 3-2c：本批**填过补录**的票号同事务落留痕（dim=import_backfill）。
        # 为什么写：复核页「已补录」筛选的判据 `_row_backfilled` 只看内存集合 `_backfills`，
        #   而 `rebuild_period_data` 刻意 `backfills=[]`（补录条目已写进
        #   `invoice(source='manual')` 并随本事务落库）⇒「导入后」模式点「已补录」永远空表，
        #   用户会以为上次补的票白补了。留痕只记票号（内容在 invoice/charge_detail/collection）。
        # 事务：同款 —— 同一事务；先清本账期本 dim，覆盖式重导时旧留痕整体失效。
        clear_backfill_hints(conn, period)
        backfill_n = save_backfill_hints(conn, period, data.get("backfilled"))

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
        "deferred_count": len(data.get("deferred") or []),
        # 阶段 3 B2b：随本批同一事务写入的补录票数（供复核页汇总提示）
        "backfill_count": len(backfills or []),
        # 批 3-1：随本批同一事务写入的「导入时确认」留痕条数（anomaly_note/dim=import_confirm）
        "confirmation_count": confirm_n,
        # 批 3-2b：随本批同一事务写入的「导入时修改」留痕条数（anomaly_note/dim=import_edit）
        "edit_hint_count": edit_hint_n,
        # 批 3-2c：随本批同一事务写入的「已补录」留痕条数（dim=import_backfill）
        "backfill_hint_count": backfill_n,
        # A10：确认收款时会「超额收款」的已在库票（台账信息错误，未写入收款）。
        # 供调用方提示用户核对台账（复核页在阶段 2-2 把它标成问题行）。
        "over_collected": over_collected,
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
        # 费用台账导入：经手人(真实发生/字段 handler)不校验花名册；
        # 仅校验经办人(承担人员/字段 actual_handler)
        names = []
        for it in items:
            if it["actual_handler"]:
                names.append(it["actual_handler"])
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
