"""补录（期初/历史应收）发票引擎

待补录 = 所有「被引用但 invoice 表中没有」的发票号（红字 orig_invoice_no / refund.orig_invoice_no）。
1.1（红字原票缺失）与 1.2（应收对应缺失）合并为同一检测逻辑，列表不区分。
补录写入 invoice(source='manual') + charge_detail(经办人/开票金额) + collection(已收/日期)，
与现有手工补录完全一致，不影响其他页面（收款表/结算/退款判定）。
"""
from __future__ import annotations

from typing import Dict, List

from app.db import get_conn
from app.engine.backfill import norm_type, staff_type_of


def list_pending_backfill(conn=None) -> List[Dict]:
    """待补录：被引用但 invoice 表中不存在的发票号（统一检测 1.1+1.2）。"""
    own = conn is None
    if own:
        conn = get_conn()
    try:
        rows: List[Dict] = []
        seen = set()
        for r in conn.execute(
            "SELECT DISTINCT orig_invoice_no FROM ("
            "  SELECT orig_invoice_no FROM invoice WHERE orig_invoice_no IS NOT NULL AND orig_invoice_no <> ''"
            "  UNION"
            "  SELECT orig_invoice_no FROM refund WHERE orig_invoice_no IS NOT NULL AND orig_invoice_no <> ''"
            ")"
        ):
            orig = r["orig_invoice_no"]
            if orig in seen:
                continue
            seen.add(orig)
            if conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (orig,)).fetchone():
                continue  # 已存在，无需补录
            reds = [
                x["invoice_no"] for x in conn.execute(
                    "SELECT invoice_no FROM invoice WHERE orig_invoice_no=? AND total_amount < 0", (orig,)
                )
            ]
            is_red = bool(reds) or conn.execute(
                "SELECT 1 FROM refund WHERE orig_invoice_no=?", (orig,)
            ).fetchone() is not None
            rows.append({
                "invoice_no": orig,
                "status": "已被红冲" if is_red else "正常",
                "collected": 0.0,
                "red_invoices": reds,
            })
        return rows
    finally:
        if own:
            conn.close()


def list_backfilled(conn=None) -> List[Dict]:
    """已补录：source='manual' 的发票。"""
    own = conn is None
    if own:
        conn = get_conn()
    try:
        rows: List[Dict] = []
        for inv in conn.execute(
            "SELECT invoice_no, invoice_date, buyer, total_amount, created_at "
            "FROM invoice WHERE source='manual' ORDER BY invoice_date DESC, invoice_no"
        ):
            collected = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM collection WHERE invoice_no=?", (inv["invoice_no"],)
            ).fetchone()[0]
            is_red = conn.execute(
                "SELECT 1 FROM invoice WHERE orig_invoice_no=? AND total_amount < 0", (inv["invoice_no"],)
            ).fetchone() is not None
            rows.append({
                "invoice_no": inv["invoice_no"],
                "invoice_date": inv["invoice_date"] or "",
                "buyer": inv["buyer"] or "",
                "total_amount": inv["total_amount"],
                "status": "已被红冲" if is_red else "正常",
                "collected": round(collected, 2),
                "created_at": inv["created_at"] or "",
            })
        return rows
    finally:
        if own:
            conn.close()


def load_invoice_detail(invoice_no: str) -> Dict:
    """读取一张发票的补录明细（编辑用）。"""
    conn = get_conn()
    try:
        inv = conn.execute("SELECT * FROM invoice WHERE invoice_no=?", (invoice_no,)).fetchone()
        if inv is None:
            return {}
        cds = conn.execute(
            "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=? ORDER BY id", (invoice_no,)
        ).fetchall()
        cols = conn.execute(
            "SELECT amount, receipt_date, person_name FROM collection "
            "WHERE invoice_no=? AND source='manual' ORDER BY id", (invoice_no,)
        ).fetchall()
        billing_map = {cd["person_name"]: cd["billing_amount"] for cd in cds}
        handlers: List[Dict] = []
        if cols:
            for c in cols:
                handlers.append({
                    "name": c["person_name"],
                    "billing": billing_map.get(c["person_name"], 0.0),
                    "received": c["amount"],
                    "date": c["receipt_date"] or "",
                })
        else:
            for cd in cds:
                handlers.append({
                    "name": cd["person_name"],
                    "billing": cd["billing_amount"],
                    "received": 0.0,
                    "date": "",
                })
        return {
            "invoice_no": inv["invoice_no"],
            "invoice_date": inv["invoice_date"] or "",
            "buyer": inv["buyer"] or "",
            "total_amount": inv["total_amount"],
            "handlers": handlers,
        }
    finally:
        conn.close()


def save_backfill(data: Dict, editing: bool = False) -> None:
    """保存补录（新增/编辑通用）。

    data: {invoice_no, invoice_date, buyer, total_amount,
           handlers:[{name, billing, received, date}]}
    写入 invoice(source='manual') + 按经办人聚合的 charge_detail
    + 明细表每行（已收金额>0 且有收款日期）一笔 collection。
    被红字引用时，只要发票号对齐即可自动消除「待补录」。
    """
    no = (data.get("invoice_no") or "").strip()
    if not no:
        raise ValueError("发票号码不能为空")
    if not (data.get("invoice_date") or "").strip():
        raise ValueError("开票日期不能为空")
    total = float(data.get("total_amount") or 0)
    conn = get_conn()
    try:
        # 清旧 manual 数据（允许重复保存/编辑）
        conn.execute("DELETE FROM charge_detail WHERE invoice_no=? AND source='manual'", (no,))
        conn.execute("DELETE FROM collection WHERE invoice_no=? AND source='manual'", (no,))
        exist = conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (no,)).fetchone()
        if exist:
            conn.execute(
                "UPDATE invoice SET invoice_date=?, buyer=?, total_amount=?, source='manual' "
                "WHERE invoice_no=?",
                (data["invoice_date"].strip(), (data.get("buyer") or "").strip(), total, no),
            )
        else:
            conn.execute(
                "INSERT INTO invoice "
                "(invoice_no, invoice_date, buyer, total_amount, kind, source, created_at) "
                "VALUES (?,?,?,?,?,?, datetime('now','localtime'))",
                (no, data["invoice_date"].strip(), (data.get("buyer") or "").strip(), total, "", "manual"),
            )
        # charge_detail：按经办人聚合开票金额
        agg: Dict[str, float] = {}
        for h in data.get("handlers", []):
            nm = (h.get("name") or "").strip()
            if not nm:
                continue
            agg[nm] = agg.get(nm, 0.0) + float(h.get("billing") or 0)
        for nm, billing in agg.items():
            ptype = norm_type(staff_type_of(conn, nm))
            conn.execute(
                "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source, person_type) "
                "VALUES (?,?,?,?,?)",
                (no, nm, billing, "manual", ptype),
            )
        # collection：明细表每行（已收金额>0 且有收款日期）写一笔
        for h in data.get("handlers", []):
            nm = (h.get("name") or "").strip()
            amt = float(h.get("received") or 0)
            rdate = (h.get("date") or "").strip()
            if nm and amt > 0.001 and rdate:
                conn.execute(
                    "INSERT INTO collection (invoice_no, amount, receipt_date, person_name, source, note) "
                    "VALUES (?,?,?,?,?,?)",
                    (no, amt, rdate, nm, "manual", "补录"),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_backfill(invoice_no: str) -> None:
    """删除一张补录发票（仅 manual 来源）。被退款引用时阻止。"""
    conn = get_conn()
    try:
        if conn.execute("SELECT 1 FROM refund WHERE orig_invoice_no=?", (invoice_no,)).fetchone():
            raise ValueError(
                f"发票 {invoice_no} 存在已确认的退款记录，无法删除。请先删除对应退款记录。"
            )
        conn.execute("DELETE FROM charge_detail WHERE invoice_no=? AND source='manual'", (invoice_no,))
        conn.execute("DELETE FROM collection WHERE invoice_no=? AND source='manual'", (invoice_no,))
        conn.execute("DELETE FROM invoice WHERE invoice_no=? AND source='manual'", (invoice_no,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
