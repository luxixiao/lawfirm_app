"""统一导入入口：批次管理 / 事务写入 / 校验 / 原始文件存档

规则（已确认）：
- 覆盖式导入：同月同类型已有 active 批次 → 先撤销旧批次再导入
- 撤销导入：删除该批次所有数据（仅 import 来源），manual 数据不受影响
- 经办人必须命中职工花名册（"公共"等白名单除外），未命中阻止导入
- 台账 sheet1+sheet2 合计 = 该月销项价税合计合计（销项须先导入）
- 跨月重复发票：collection 以最新台账记录为准（先删该票 import 旧记录再插新）
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from app.db import get_conn
from app.engine.backfill import norm_type, staff_type_of
from app.importer.expense_import import parse_expense_file
from app.importer.invoice_import import parse_invoice_file
from app.importer.ledger_import import parse_ledger_file
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
    conn = get_conn()
    try:
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
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"type": "invoice", "period": period, "count": len(invoices)}


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
                "sheet": "problem_fix",
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
                "receipts_override": d["receipts"],
            })
        else:  # prepayment
            data["prepayments"].append({
                "received_date": d["received_date"] or None,
                "buyer": p.get("buyer", ""),
                "amount": d["amount"],
                "person_text": d["person_text"],
                "remark": "",
                "case_no": "",
            })


def import_ledger_file(path: str, period: str,
                       on_problems: callable | None = None) -> Dict:
    """导入发票台账。

    on_problems: 可选回调 (problems: list) -> resolved: list | None。
    解析失败的问题行交给回调处理（如弹修正对话框），返回
    [{index, action: 'fix'|'skip', data}]；返回 None 表示用户取消导入。
    """
    _auto_snapshot()
    data = parse_ledger_file(path, period)
    if data["problems"] and on_problems is not None:
        resolved = on_problems(data["problems"])
        if resolved is None:
            raise ImportError_("已取消导入")
        _apply_resolved(data, resolved)
        data["problems"] = []
    conn = get_conn()
    try:
        # ---- 校验 1：sheet1+sheet2 合计 = 销项合计（本月销项须已导入）----
        # strftime 规范化匹配：兼容历史非零填充日期（2024-9-15 也算入 2024-09）
        inv_total = conn.execute(
            "SELECT COALESCE(SUM(total_amount),0) FROM invoice WHERE strftime('%Y-%m', invoice_date) = ?",
            (period,),
        ).fetchone()[0]
        s12 = data["sheet12_total"]
        if abs(inv_total - s12) > 0.01:
            # 允许销项未导入的情况单独提示
            if inv_total == 0 and "sheet1" not in data["sheet_totals"]:
                pass
            raise ImportError_(
                f"校验失败: 台账 sheet1+sheet2 合计({s12:g}) ≠ 销项文档本月价税合计({inv_total:g})。"
                f"请确认已先导入 {period} 销项文档。"
            )

        # ---- 校验 2：经办人必须在花名册 ----
        all_handlers: List[str] = []
        for inv in data["invoices"]:
            all_handlers += [h[0] for h in inv["handlers"]]
        missing = _validate_handler_names(conn, all_handlers, "发票台账")
        if missing:
            raise ImportError_(
                f"经办人不在职工花名册中（请先在员工管理中添加）: {', '.join(sorted(set(missing)))}"
            )

        # ---- 覆盖式导入 ----
        _drop_active_batch(conn, "ledger", period)
        archive = _archive_file(path, "ledger", period)
        batch_id = _new_batch(conn, "ledger", period, Path(path).name, archive, "")

        for inv in data["invoices"]:
            no = inv["invoice_no"]
            # 发票 upsert：不存在则创建（期外发票），存在则补案号
            exist = conn.execute("SELECT case_no FROM invoice WHERE invoice_no=?", (no,)).fetchone()
            if exist:
                if inv.get("case_no") and not exist["case_no"]:
                    conn.execute("UPDATE invoice SET case_no=? WHERE invoice_no=?", (inv["case_no"], no))
            else:
                conn.execute(
                    """INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, case_no,
                       source, import_batch_id, orig_invoice_no)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (no, inv["invoice_date"], inv["buyer"], inv["total_amount"],
                     inv.get("case_no"), "import", batch_id, ""),
                )
            # 经办人拆分（已存在则跳过）
            for name, amount in inv["handlers"]:
                r = conn.execute(
                    "SELECT id FROM charge_detail WHERE invoice_no=? AND person_name=?", (no, name)
                ).fetchone()
                if not r:
                    conn.execute(
                        "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source, import_batch_id, person_type) VALUES (?,?,?,?,?,?)",
                        (no, name, amount, "import", batch_id,
                         norm_type(staff_type_of(conn, name))),
                    )
            # 收款明细：先删该发票 import 旧记录（以最新台账为准），再插入
            conn.execute("DELETE FROM collection WHERE invoice_no=? AND source='import'", (no,))
            if inv.get("receipts_override") is not None:
                # 问题行修正：收款由用户手动指定（空列表 = 无收款）
                for ym, amt in inv["receipts_override"]:
                    conn.execute(
                        "INSERT INTO collection (invoice_no, amount, receipt_date, source, import_batch_id) VALUES (?,?,?,?,?)",
                        (no, amt, ym, "import", batch_id),
                    )
                continue
            rem = inv["remark"]
            if inv["is_red"]:
                # 红字发票：不产生收款（退款走 refund 手动确认）
                pass
            elif rem["pure_date"]:
                conn.execute(
                    "INSERT INTO collection (invoice_no, amount, receipt_date, source, import_batch_id) VALUES (?,?,?,?,?)",
                    (no, inv["total_amount"], rem["pure_date"], "import", batch_id),
                )
            else:
                for ym, amt in rem["receipts"]:
                    if amt == 0:
                        amt = inv["total_amount"]
                    conn.execute(
                        "INSERT INTO collection (invoice_no, amount, receipt_date, source, import_batch_id) VALUES (?,?,?,?,?)",
                        (no, amt, ym, "import", batch_id),
                    )

        # 预收款（sheet4）
        for pp in data["prepayments"]:
            conn.execute(
                """INSERT INTO prepayment (received_date, buyer, amount, person_text, case_no, remark,
                   source, import_batch_id) VALUES (?,?,?,?,?,?,?,?)""",
                (pp["received_date"], pp["buyer"], pp["amount"], pp["person_text"],
                 pp["case_no"], pp["remark"], "import", batch_id),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "type": "ledger",
        "period": period,
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
                f"费用类型不在维护名单中（请先到「台账数据→费用归类」添加或归类）: {', '.join(sorted(set(unknown)))}"
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
