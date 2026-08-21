"""收款聚合引擎：发票维度 + 经办人维度

口径（已确认）：
- 发票已收金额：正数发票 = Σcollection；红字发票 = −Σrefund（退款合计为负）
- 发票剩余应收 = 价税合计 − 已收金额（统一公式，含红字发票）
- 经办人已收：全额 = 开票金额；部分 = 平均分摊+收齐退出（split.allocate_invoice）
- 经办人剩余应收 = 经办人开票金额 − 经办人已收
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.db import get_conn
from app.engine.split import allocate_invoice


def _collected_by_invoice(conn) -> Dict[str, float]:
    """{发票号: 已收金额}（正数发票 = Σcollection）"""
    return {
        r["invoice_no"]: r["total"]
        for r in conn.execute(
            "SELECT invoice_no, SUM(amount) AS total FROM collection GROUP BY invoice_no"
        )
    }


def _refunded_by_invoice(conn) -> Dict[str, float]:
    """{红字发票号: 退款合计}"""
    return {
        r["red_invoice_no"]: r["total"]
        for r in conn.execute(
            "SELECT red_invoice_no, SUM(refund_amount) AS total FROM refund GROUP BY red_invoice_no"
        )
    }


def invoice_rows(conn=None, period: str | None = None, buyer: str | None = None,
                 source: str | None = None, invoice_no: str | None = None) -> List[Dict]:
    """发票收款总表数据

    列：开具日期、发票号码、购买方名称、价税合计、经办人、已收金额、剩余应收、备注
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        collected = _collected_by_invoice(conn)
        refunded = _refunded_by_invoice(conn)

        where = []
        params: List = []
        if period:
            where.append("invoice_date LIKE ?")
            params.append(period + "%")
        if buyer:
            where.append("buyer LIKE ?")
            params.append("%" + buyer + "%")
        if source:
            where.append("source = ?")
            params.append(source)
        if invoice_no:
            where.append("invoice_no LIKE ?")
            params.append("%" + invoice_no + "%")
        sql = "SELECT * FROM invoice"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY invoice_date, invoice_no"

        rows = []
        for inv in conn.execute(sql, params):
            no = inv["invoice_no"]
            is_red = inv["total_amount"] < 0
            if is_red:
                got = -refunded.get(no, 0.0)  # 退款合计为负 = 已退款
            else:
                got = collected.get(no, 0.0)
            remain = round(inv["total_amount"] - got, 2)
            cd = conn.execute(
                "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=? ORDER BY id", (no,)
            ).fetchall()
            handlers = [r["person_name"] for r in cd]
            # 备注 = 收款日期 + 经办人占据金额（如"2025-01 张三1000王五2000"）
            dates = sorted({r["receipt_date"] for r in conn.execute(
                "SELECT receipt_date FROM collection WHERE invoice_no=?", (no,)
            )})
            amount_parts = [f"{r['person_name']}{r['billing_amount']:g}" for r in cd]
            remark = ("、".join(dates) + " " if dates else "") + " ".join(amount_parts)
            rows.append({
                "invoice_no": no,
                "invoice_date": inv["invoice_date"],
                "buyer": inv["buyer"],
                "total_amount": inv["total_amount"],
                "handlers": "、".join(handlers),
                "collected": round(got, 2),
                "remain": remain,
                "remark": remark,
                "is_red": is_red,
                "orig_invoice_no": inv["orig_invoice_no"],
                "case_no": inv["case_no"],
                "source": inv["source"],
            })
        return rows
    finally:
        if own:
            conn.close()


def handler_rows(conn=None, person: str | None = None, period: str | None = None,
                 buyer: str | None = None, source: str | None = None) -> List[Dict]:
    """经办人发票收款总表 / 经办人发票收款表（单经办人）数据

    列：开具日期、发票号码、购买方名称、开票总额、经办人、开票金额、已收金额、剩余应收、备注
    一票多经办人拆多行。
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        collected = _collected_by_invoice(conn)
        refunded = _refunded_by_invoice(conn)

        where = []
        params: List = []
        if person:
            where.append("cd.person_name = ?")
            params.append(person)
        if period:
            where.append("i.invoice_date LIKE ?")
            params.append(period + "%")
        if buyer:
            where.append("i.buyer LIKE ?")
            params.append("%" + buyer + "%")
        if source:
            where.append("i.source = ?")
            params.append(source)
        sql = """SELECT i.invoice_no, i.invoice_date, i.buyer, i.total_amount,
                        i.orig_invoice_no, i.case_no, i.source,
                        cd.person_name, cd.billing_amount
                 FROM charge_detail cd JOIN invoice i ON cd.invoice_no = i.invoice_no"""
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY i.invoice_date, i.invoice_no, cd.id"

        # 按发票分组：一次分摊
        grouped: Dict[str, list] = {}
        order: List[str] = []
        for r in conn.execute(sql, params):
            no = r["invoice_no"]
            if no not in grouped:
                grouped[no] = []
                order.append(no)
            grouped[no].append(r)

        rows: List[Dict] = []
        for no in order:
            items = grouped[no]
            inv = items[0]
            is_red = inv["total_amount"] < 0
            receipts = [(r["receipt_date"], r["amount"]) for r in conn.execute(
                "SELECT receipt_date, amount FROM collection WHERE invoice_no=? ORDER BY id", (no,)
            )]
            handlers = [(r["person_name"], r["billing_amount"]) for r in items]
            if is_red:
                # 红字发票：经办人已收 = 0（退款走 refund 手动确认，不参与分摊）
                got_map = {name: 0.0 for name, _ in handlers}
            else:
                allocated = allocate_invoice(handlers, receipts)
                got_map = {name: got for name, got in allocated}

            receipt_dates = sorted({d for d, _ in receipts})
            for name, billing in handlers:
                got = got_map.get(name, 0.0)
                rows.append({
                    "invoice_no": no,
                    "invoice_date": inv["invoice_date"],
                    "buyer": inv["buyer"],
                    "total_amount": inv["total_amount"],
                    "person_name": name,
                    "billing_amount": round(billing, 2),
                    "collected": round(got, 2),
                    "remain": round(billing - got, 2),
                    "receipt_dates": "、".join(receipt_dates),
                    "is_red": is_red,
                    "orig_invoice_no": inv["orig_invoice_no"],
                    "case_no": inv["case_no"],
                })
        return rows
    finally:
        if own:
            conn.close()
