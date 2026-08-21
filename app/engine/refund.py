"""退款判定引擎

规则（需求 2.9，已确认）：
- 红字发票对应原正数发票当月开具 → 不退款
- 原正数发票以前月份开具且未收款 → 不退款
- 原正数发票以前月份开具且已收款（部分/全部）→ 需退款（金额 = 红字发票金额绝对值，
  退款日期手动确认）
- 跨年原票未导入（无期初文档）→ 标记"原票未导入"，手动补录后自动判定

refund 记录由用户手动确认后创建（可多次、可部分），本引擎只做判定。
"""
from __future__ import annotations

from typing import Dict, List

from app.db import get_conn


def evaluate_red_invoices(conn=None) -> List[Dict]:
    """扫描全部红字发票，输出退款判定列表

    每项: {red_invoice_no, red_amount, orig_invoice_no, orig_invoice_date,
           orig_collected, status, refund_amount}
    status:
        no_orig            无原票关联（备注未解析出原票号）
        orig_missing       原票未导入（跨期/无期初文档，需手动补录）
        same_month         原票当月开具，不退款
        orig_uncollected   原票以前月份且未收款，不退款
        need_refund        需退款（原票以前月份且已收款）
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        collected: Dict[str, float] = {
            r["invoice_no"]: r["total"]
            for r in conn.execute("SELECT invoice_no, SUM(amount) AS total FROM collection GROUP BY invoice_no")
        }
        rows: List[Dict] = []
        for inv in conn.execute(
            "SELECT * FROM invoice WHERE total_amount < 0 ORDER BY invoice_date"
        ):
            no = inv["invoice_no"]
            orig = inv["orig_invoice_no"]
            item = {
                "red_invoice_no": no,
                "red_amount": inv["total_amount"],
                "orig_invoice_no": orig,
                "orig_invoice_date": None,
                "orig_collected": None,
                "status": "no_orig",
                "refund_amount": round(abs(inv["total_amount"]), 2),
            }
            if orig:
                oi = conn.execute("SELECT * FROM invoice WHERE invoice_no=?", (orig,)).fetchone()
                if oi is None:
                    item["status"] = "orig_missing"
                else:
                    item["orig_invoice_date"] = oi["invoice_date"]
                    orig_got = collected.get(orig, 0.0)
                    item["orig_collected"] = round(orig_got, 2)
                    if inv["invoice_date"][:7] == oi["invoice_date"][:7]:
                        item["status"] = "same_month"
                    elif orig_got <= 0.01:
                        item["status"] = "orig_uncollected"
                    else:
                        item["status"] = "need_refund"
            rows.append(item)
        return rows
    finally:
        if own:
            conn.close()
