"""收款聚合引擎：发票维度 + 经办人维度

口径（已确认）：
- 发票已收金额：正数发票 = Σcollection；红字发票 = −Σrefund（退款合计为负）
- 发票剩余应收 = 价税合计 − 已收金额（统一公式，含红字发票）
- 经办人已收：全额 = 开票金额；部分 = 平均分摊+收齐退出（split.allocate_invoice）
- 经办人剩余应收 = 经办人开票金额 − 经办人已收
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

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


def _refund_months_by_invoice(conn) -> Dict[str, List[str]]:
    """{红字发票号: [退款月份 YYYY-MM, ...]}（按退款记录，可能多笔部分退款）"""
    m: Dict[str, List[str]] = {}
    for r in conn.execute("SELECT red_invoice_no, refund_date FROM refund"):
        d = (r["refund_date"] or "")[:7]   # YYYY-MM
        if d:
            m.setdefault(r["red_invoice_no"], []).append(d)
    return m


def invoice_rows(conn=None, period: str | None = None, keyword: str | None = None,
                 source: str | None = None, invoice_no: str | None = None) -> List[Dict]:
    """发票收款总表数据

    列：开具日期、发票号码、购买方名称、价税合计、经办人(含金额)、已收金额、剩余应收、收款日期
    keyword：发票号码 OR 购买方名称 OR 经办人 OR 价税合计/已收/剩余 金额 模糊检索
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        collected = _collected_by_invoice(conn)
        refunded = _refunded_by_invoice(conn)
        refund_months = _refund_months_by_invoice(conn)

        where = []
        params: List = []
        if period:
            # 年份（4 位）按年过滤；YYYY-MM 精确月；2 位月份跨年匹配该月
            if len(period) == 4:
                where.append("strftime('%Y', invoice_date) = ?")
            elif len(period) == 7:
                where.append("strftime('%Y-%m', invoice_date) = ?")
            else:
                where.append("strftime('%m', invoice_date) = ?")
            params.append(period)
        if keyword:
            # 发票号码/购方名/经办人/金额（价税合计·已收·剩余）
            where.append(
                "(buyer LIKE ? OR invoice_no LIKE ? "
                "OR invoice_no IN (SELECT invoice_no FROM charge_detail WHERE person_name LIKE ?) "
                "OR CAST(total_amount AS TEXT) LIKE ? "
                "OR CAST((SELECT COALESCE(SUM(amount),0) FROM collection WHERE collection.invoice_no=invoice.invoice_no) AS TEXT) LIKE ? "
                "OR CAST((total_amount - (SELECT COALESCE(SUM(amount),0) FROM collection WHERE collection.invoice_no=invoice.invoice_no)) AS TEXT) LIKE ?)")
            params += ["%" + keyword + "%"] * 2          # buyer, invoice_no
            params.append("%" + keyword + "%")            # person_name
            params += ["%" + keyword + "%"] * 3          # total_amount, 已收, 剩余
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
        # 一次取全部经办人明细与收款日期，按发票号分组（消除逐发票 N+1 查询）
        cds_by_inv: Dict[str, list] = {}
        for r in conn.execute(
                "SELECT invoice_no, person_name, billing_amount FROM charge_detail ORDER BY invoice_no, id"):
            cds_by_inv.setdefault(r["invoice_no"], []).append(r)
        from collections import defaultdict
        month_amt_by_inv: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for r in conn.execute("SELECT invoice_no, receipt_date, amount FROM collection ORDER BY invoice_no, id"):
            no = r["invoice_no"]
            ym = (r["receipt_date"] or "")[:7]  # YYYY-MM
            if ym:
                month_amt_by_inv[no][ym] += r["amount"]

        # 红字发票映射：{原发票号: [(红冲年月 YYYY-MM, 红冲金额绝对值), ...]}
        # 用于"备注"列（原票被红冲提示）与"剩余应收"红冲抵消。
        red_map: Dict[str, List] = {}
        for r in conn.execute(
            "SELECT orig_invoice_no, invoice_date, total_amount FROM invoice "
            "WHERE total_amount < 0 AND orig_invoice_no IS NOT NULL AND orig_invoice_no <> ''"
        ):
            red_map.setdefault(r["orig_invoice_no"], []).append(
                (r["invoice_date"][:7] if r["invoice_date"] else "", abs(r["total_amount"]))
            )

        for inv in conn.execute(sql, params):
            no = inv["invoice_no"]
            is_red = inv["total_amount"] < 0
            if is_red:
                got = -refunded.get(no, 0.0)  # 退款合计为负 = 已退款
            else:
                got = collected.get(no, 0.0)
            reds = red_map.get(no, [])
            red_abs = sum(abs_amt for _ym, abs_amt in reds)
            if is_red:
                # 红字发票本身是冲抵凭证，剩余应收恒为 0
                remain = 0.0
                remark = ""
            else:
                if reds:
                    # 原票被红冲：红冲金额 ≥ 已收时，原票与红字发票互相抵消，剩余应收记 0；
                    # 部分红冲（红冲 < 已收）时，剩余应收 = 票面 − (已收 − 红冲)。
                    if red_abs >= got - 1e-9:
                        remain = 0.0
                    else:
                        remain = round(inv["total_amount"] - got + red_abs, 2)
                else:
                    remain = round(inv["total_amount"] - got, 2)
                remark = "、".join(f"{ym}被红冲" for ym, _ in reds if ym) if reds else ""
            cd = cds_by_inv.get(no, [])
            handlers = [r["person_name"] for r in cd]
            # 经办人 + 开票金额（如"张三3000、王五5000"）
            handlers_amount = "、".join(f"{r['person_name']}{r['billing_amount']:g}" for r in cd)
            # 收款日期：按收款月份聚合金额（如"2025-2+1000、2025-3+2000"）；
            # 红字发票已确认退款时，并入退款月份（标记"(退)"），由视图标红。
            month_map = month_amt_by_inv.get(no, {})
            parts = [f"{ym}+{amt:g}" for ym, amt in sorted(month_map.items())]
            rm = refund_months.get(no, [])
            for d in sorted(set(rm)):
                parts.append(f"{d}(退)")
            receipt_dates_str = "、".join(parts)
            rows.append({
                "invoice_no": no,
                "invoice_date": inv["invoice_date"],
                "buyer": inv["buyer"],
                "total_amount": inv["total_amount"],
                "handlers": "、".join(handlers),
                "handlers_amount": handlers_amount,
                "collected": round(got, 2),
                "remain": remain,
                "receipt_dates_str": receipt_dates_str,
                "remark": remark,
                "is_red": is_red,
                "is_refunded": bool(rm),
                "orig_invoice_no": inv["orig_invoice_no"],
                "case_no": inv["case_no"],
                "source": inv["source"],
            })
        return rows
    finally:
        if own:
            conn.close()


def handler_rows(conn=None, person: str | None = None, period: str | None = None,
                 keyword: str | None = None, source: str | None = None) -> List[Dict]:
    """经办人发票收款总表 / 经办人发票收款表（单经办人）数据

    列：开具日期、发票号码、购买方名称、开票总额、经办人、开票金额、已收金额、剩余应收、备注
    一票多经办人拆多行。
    keyword：全字段检索（发票号/购方名/经办人/案号/金额：开票总额·开票金额·已收·剩余）。
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        collected = _collected_by_invoice(conn)
        refunded = _refunded_by_invoice(conn)
        refund_months = _refund_months_by_invoice(conn)

        # 红字发票映射：{原发票号: [(红冲年月 YYYY-MM, 红冲金额绝对值), ...]}
        # 用于"备注"列（原票被红冲提示）与"剩余应收"红冲抵消，逻辑对齐 invoice_rows。
        red_map: Dict[str, List] = {}
        for r in conn.execute(
            "SELECT orig_invoice_no, invoice_date, total_amount FROM invoice "
            "WHERE total_amount < 0 AND orig_invoice_no IS NOT NULL AND orig_invoice_no <> ''"
        ):
            red_map.setdefault(r["orig_invoice_no"], []).append(
                (r["invoice_date"][:7] if r["invoice_date"] else "", abs(r["total_amount"]))
            )

        where = []
        params: List = []
        if person:
            where.append("cd.person_name = ?")
            params.append(person)
        if period:
            # 年份（4 位）按年过滤；YYYY-MM 精确月；2 位月份跨年匹配该月
            if len(period) == 4:
                where.append("strftime('%Y', i.invoice_date) = ?")
            elif len(period) == 7:
                where.append("strftime('%Y-%m', i.invoice_date) = ?")
            else:
                where.append("strftime('%m', i.invoice_date) = ?")
            params.append(period)
        if keyword:
            kw = f"%{keyword}%"
            where.append(
                "(i.invoice_no LIKE ? OR i.buyer LIKE ? OR cd.person_name LIKE ? "
                "OR i.case_no LIKE ? OR CAST(i.total_amount AS TEXT) LIKE ? "
                "OR CAST(cd.billing_amount AS TEXT) LIKE ? "
                "OR CAST((SELECT COALESCE(SUM(amount),0) FROM collection c WHERE c.invoice_no=i.invoice_no) AS TEXT) LIKE ? "
                "OR CAST((cd.billing_amount - (SELECT COALESCE(SUM(amount),0) FROM collection c WHERE c.invoice_no=i.invoice_no)) AS TEXT) LIKE ?)"
            )
            params.extend([kw] * 8)
        if source:
            where.append("i.source = ?")
            params.append(source)
        sql = """SELECT i.invoice_no, i.invoice_date, i.buyer, i.total_amount,
                        i.orig_invoice_no, i.case_no, i.source,
                        i.src_sheet, i.src_row, i.import_batch_id,
                        cd.person_name, cd.billing_amount, cd.person_type, cd.received_override
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

        # 一次取全部收款明细，按发票号分组（消除逐发票 N+1 查询）
        receipts_by_inv: Dict[str, List[Tuple[str, float]]] = {}
        for r in conn.execute(
                "SELECT invoice_no, receipt_date, amount FROM collection ORDER BY invoice_no, id"):
            receipts_by_inv.setdefault(r["invoice_no"], []).append((r["receipt_date"], r["amount"]))

        rows: List[Dict] = []
        for no in order:
            items = grouped[no]
            inv = items[0]
            is_red = inv["total_amount"] < 0
            receipts = receipts_by_inv.get(no, [])
            reds = red_map.get(no, [])
            red_abs = sum(abs_amt for _ym, abs_amt in reds)
            inv_got = sum(amt for _d, amt in receipts)  # 发票级已收合计，用于红冲抵消判定
            handlers = [(r["person_name"], r["billing_amount"], r["person_type"] or "",
                        r["received_override"]) for r in items]
            if is_red:
                # 红字发票已确认退款：经办人已收 = 退款金额按开票金额比例分摊（为负），与发票维度已收=-退款 对齐
                R = refunded.get(no, 0.0)
                T = abs(inv["total_amount"]) or 0.0
                got_map = {}
                for name, billing, _pt, _ov in handlers:
                    share = (R * abs(billing) / T) if T else 0.0
                    got_map[name] = -round(share, 2)
            else:
                override_map = {name: ov for name, _, _, ov in handlers if ov is not None}
                allocated = allocate_invoice(
                    [(n, b) for n, b, _, _ in handlers], receipts,
                    override_map or None,
                )
                got_map = {name: got for name, got in allocated}

            receipt_dates = sorted({d for d, _ in receipts})
            rm = refund_months.get(no, [])
            if rm:
                # 红字发票已确认退款：收款月并入退款月份（由视图标红）
                receipt_dates = sorted(set(receipt_dates) | set(rm))
            for name, billing, ptype, _ov in handlers:
                got = got_map.get(name, 0.0)
                if is_red:
                    # 红字发票本身是冲抵凭证，经办人剩余应收恒为 0
                    remain = 0.0
                    remark = ""
                else:
                    if reds:
                        # 原票被红冲：红冲金额 ≥ 发票级已收时，原票与红字互相抵消，剩余应收记 0；
                        # 部分红冲（红冲 < 已收）时，按开票金额比例分摊红冲金额逐经办人抵消。
                        if red_abs >= inv_got - 1e-9:
                            remain = 0.0
                        else:
                            T = inv["total_amount"] or 0.0
                            share = (red_abs * (billing / T)) if T else 0.0
                            remain = round(billing - got + share, 2)
                    else:
                        remain = round(billing - got, 2)
                    remark = "、".join(f"{ym}被红冲" for ym, _ in reds if ym) if reds else ""
                rows.append({
                    "invoice_no": no,
                    "invoice_date": inv["invoice_date"],
                    "buyer": inv["buyer"],
                    "total_amount": inv["total_amount"],
                    "person_name": name,
                    "person_type": ptype,
                    "billing_amount": round(billing, 2),
                    "collected": round(got, 2),
                    "remain": remain,
                    "remark": remark,
                    "receipt_dates": "、".join(receipt_dates),
                    "is_red": is_red,
                    "is_refunded": bool(rm),
                    "orig_invoice_no": inv["orig_invoice_no"],
                    "case_no": inv["case_no"],
                    "src_sheet": inv["src_sheet"] or "",
                    "src_row": inv["src_row"] or 0,
                    "import_batch_id": inv["import_batch_id"],
                })
        return rows
    finally:
        if own:
            conn.close()
