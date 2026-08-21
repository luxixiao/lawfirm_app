"""个人结算总表计算引擎

口径（已与需求方逐项确认）：
- 每人一份表，行=项目 × 列=1~12月+合计
- 一、上年结余结转：留空
- 二、本月收款金额（净额=①+②+③+④+⑤，退为负）：
    ①本月开收（开票月=收款月=本月）
    ②收本年（收款月=本月，开票本年且早于本月）
    ③收上年（收款月=本月，开票上年）
    ④退本年（退款月=本月，原票本年，负数）
    ⑤退上年（退款月=本月，原票上年，负数）
- 三、本月开具发票金额（=⑥+⑦+⑧+⑨，红冲为负）：
    ⑥本月开收（本月开票且本月已收）
    ⑦本月未收（本月开票且本月未收）
    ⑧红冲本年（本月红字发票，原票本年，负数）
    ⑨红冲上年（本月红字发票，原票上年，负数）
- 四、未收款金额：各月=本月未收（=⑦）；合计=本年累计未收（本年开票净额-本年收款净额）
- 五、业务收入：合伙=本月开票净额；聘用/兼职=本月收款净额；其他类型=0
- 六、减：分成报酬及费用：按费用类型逐类列出、按月归属（按导入账期）
"""
from __future__ import annotations

from typing import Dict, List

from app.db import get_conn
from app.engine.split import allocate_receipt

MONTHS = list(range(1, 13))

STAFF_TYPE_MAP = {"合伙": "partner", "聘用": "employee", "兼职": "parttime", "其他": "other"}


def _ym(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _month_of(ym: str) -> int:
    return int(ym.split("-")[1]) if ym else 0


def _year_of(ym: str) -> int:
    return int(ym.split("-")[0]) if ym else 0


def _staff_type(conn, name: str) -> str:
    r = conn.execute("SELECT staff_type FROM staff WHERE name=? AND is_active=1", (name,)).fetchone()
    if not r:
        return "其他"
    t = (r["staff_type"] or "").strip()
    if "合伙" in t:
        return "合伙"
    if "兼职" in t:
        return "兼职"
    if "聘用" in t:
        return "聘用"
    return "其他"


def build_settlement(year: int, person: str | None = None) -> Dict:
    """计算个人结算总表数据

    Returns:
        {person: {
            'staff_type': 合伙|聘用|兼职|其他,
            'months': {m: {key: val}},   # 二/三/五 各月数值
            'uncollected_month': {m: val},   # 四 各月（本月未收）
            'uncollected_total': float,      # 四 合计（本年累计未收）
            'expenses': {expense_type: {m: val}},   # 六 费用
        }}
    """
    conn = get_conn()
    try:
        return _compute(conn, year, person)
    finally:
        conn.close()


def _new_st(conn, name: str) -> Dict:
    """标准人员结构"""
    return {
        "staff_type": _staff_type(conn, name),
        "months": {m: _empty_month() for m in MONTHS},
        "uncollected_month": {m: 0.0 for m in MONTHS},
        "uncollected_total": 0.0,
        "_uncollected_acc": 0.0,
        "expenses": {},
    }


def _empty_month() -> Dict[str, float]:
    return {k: 0.0 for k in (
        "rec_open_cur", "rec_cur_year", "rec_prev_year", "rec_refund_cur", "rec_refund_prev",
        "inv_open_received", "inv_open_uncollected", "inv_red_cur", "inv_red_prev",
        "inv_total", "income",
    )}


def _compute(conn, year: int, person: str | None) -> Dict:
    result: Dict[str, Dict] = {}
    person_filter = (person,) if person else None

    # ============ 收款分摊（按经办人）============
    # 发票维度：开票信息 + 收款记录（正数发票才有收款分摊）
    invoices = conn.execute(
        """SELECT i.invoice_no, i.invoice_date, i.total_amount, i.orig_invoice_no
           FROM invoice i WHERE i.total_amount >= 0 ORDER BY i.invoice_date, i.invoice_no"""
    ).fetchall() if not person_filter else conn.execute(
        """SELECT i.invoice_no, i.invoice_date, i.total_amount, i.orig_invoice_no
           FROM invoice i
           WHERE i.total_amount >= 0 AND EXISTS (
               SELECT 1 FROM charge_detail cd WHERE cd.invoice_no = i.invoice_no AND cd.person_name = ?
           ) ORDER BY i.invoice_date, i.invoice_no""", person_filter
    ).fetchall()

    receipts_by_inv: Dict[str, List] = {}
    # ---- 红字发票查询 + 红冲映射（未收冲减用）----
    reds = conn.execute(
        """SELECT i.invoice_no, i.invoice_date, i.total_amount, i.orig_invoice_no
           FROM invoice i WHERE i.total_amount < 0 ORDER BY i.invoice_date"""
    ).fetchall() if not person_filter else conn.execute(
        """SELECT i.invoice_no, i.invoice_date, i.total_amount, i.orig_invoice_no
           FROM invoice i WHERE i.total_amount < 0 AND EXISTS (
               SELECT 1 FROM charge_detail cd WHERE cd.invoice_no = i.invoice_no AND cd.person_name = ?)""",
        person_filter,
    ).fetchall()
    red_by_orig: Dict[str, Dict[str, float]] = {}
    for red in reds:
        if _year_of(red["invoice_date"]) != year or not red["orig_invoice_no"]:
            continue
        cds_r = conn.execute(
            "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=? ORDER BY id",
            (red["invoice_no"],),
        ).fetchall()
        for cd_r in cds_r:
            red_by_orig.setdefault(red["orig_invoice_no"], {})[cd_r["person_name"]] = abs(cd_r["billing_amount"])

    for inv in invoices:
        receipts_by_inv[inv["invoice_no"]] = conn.execute(
            "SELECT amount, receipt_date FROM collection WHERE invoice_no=? ORDER BY id",
            (inv["invoice_no"],),
        ).fetchall()

    # 经办人开票拆分
    cds_by_inv: Dict[str, List] = {}
    for inv in invoices:
        cds_by_inv[inv["invoice_no"]] = conn.execute(
            "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=? ORDER BY id",
            (inv["invoice_no"],),
        ).fetchall()

    for inv in invoices:
        no = inv["invoice_no"]
        inv_year, inv_month = _year_of(inv["invoice_date"]), _month_of(inv["invoice_date"])
        cds = cds_by_inv.get(no, [])
        if not cds:
            continue
        # 每人开票份额
        remaining = {cd["person_name"]: cd["billing_amount"] for cd in cds}
        # 逐笔收款分摊
        for rec in receipts_by_inv.get(no, []):
            amount, rec_date = rec["amount"], rec["receipt_date"]
            got = allocate_receipt(remaining, amount)
            rec_year, rec_month = _year_of(rec_date), _month_of(rec_date)
            if rec_year != year:
                continue  # 只统计本年收款
            for name, val in got.items():
                if val == 0:
                    continue
                st = result.setdefault(name, _new_st(conn, name))
                m = st["months"][rec_month]
                if rec_year == inv_year and rec_month == inv_month:
                    pass  # ①由开票循环统一计算（=本月开票已收，含预收）
                elif rec_year == year and inv_year == year and rec_month > inv_month:
                    m["rec_cur_year"] += val          # ②收本年
                elif inv_year < year:
                    m["rec_prev_year"] += val         # ③收上年
                else:
                    m["rec_cur_year"] += val          # 兜底：本年票、收晚月（含上月开本月收跨月）

        # 开票归类（含未收）
        for cd in cds:
            if inv_year != year:
                # 期外发票（上年开票本年收款已归③，不参与本年开票/未收）
                continue
            name = cd["person_name"]
            billing = cd["billing_amount"]
            st = result.setdefault(name, _new_st(conn, name))
            m = st["months"][inv_month]
            # 已收部分（该经办人在此票上的分摊累计已收，含期外预收）
            got_total = sum(_allocated_total(remaining, cds, name, receipts_by_inv.get(no, [])))
            # 被红冲的原票：按经办人冲减开票金额（红冲后作废，未收清零）
            red_cut = red_by_orig.get(no, {}).get(name, 0.0)
            uncollected = round(max(billing - red_cut, 0.0) - got_total, 2)
            if uncollected > 0.01:
                m["inv_open_uncollected"] += uncollected   # ⑦本月未收
                st["uncollected_month"][inv_month] += uncollected  # 四·本月
                st["_uncollected_acc"] += uncollected            # 四·合计（存量累计）
            # ①/⑥本月开收 = 本月开票且已收款（含当月收款与以前月份预收款）
            received = round(billing - max(uncollected, 0.0), 2)
            m["rec_open_cur"] += received          # ①
            m["inv_open_received"] += received     # ⑥
            # 本月开票总额（三小计独立计算，含预收票）
            m["inv_total"] += billing

    # ============ 红字发票（三⑧⑨）============
    for red in reds:
        red_year, red_month = _year_of(red["invoice_date"]), _month_of(red["invoice_date"])
        if red_year != year:
            continue  # 只统计本年红冲
        cds = conn.execute(
            "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=? ORDER BY id",
            (red["invoice_no"],),
        ).fetchall()
        if not cds:
            continue
        # 原票年份/月份
        orig_year, orig_month = None, None
        if red["orig_invoice_no"]:
            oi = conn.execute("SELECT invoice_date FROM invoice WHERE invoice_no=?", (red["orig_invoice_no"],)).fetchone()
            if oi:
                orig_year = _year_of(oi["invoice_date"])
                orig_month = _month_of(oi["invoice_date"])
        for cd in cds:
            name = cd["person_name"]
            st = result.setdefault(name, _new_st(conn, name))
            m = st["months"][red_month]
            val = cd["billing_amount"]  # 负数
            m["inv_total"] += val            # 三小计含红冲
            if orig_year is not None and orig_year < year:
                m["inv_red_prev"] += val     # ⑨红冲上年
            elif orig_year == year and orig_month is not None and orig_month < red_month:
                m["inv_red_cur"] += val      # ⑧红冲本年（原票本年以前月份，不含同月）

    # ============ 退款（二④⑤，按红字经办人比例分摊）============
    refunds = conn.execute(
        "SELECT red_invoice_no, refund_amount, refund_date FROM refund ORDER BY refund_date"
    ).fetchall()
    for ref in refunds:
        red_no = ref["red_invoice_no"]
        red = conn.execute("SELECT invoice_date FROM invoice WHERE invoice_no=?", (red_no,)).fetchone()
        if red is None:
            continue
        cds = conn.execute(
            "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=? ORDER BY id", (red_no,)
        ).fetchall()
        if not cds:
            continue
        total_abs = sum(abs(cd["billing_amount"]) for cd in cds) or 1.0
        refund_year = _year_of(ref["refund_date"])
        if refund_year != year:
            continue  # 只统计本年退款
        refund_month = _month_of(ref["refund_date"])
        # 原票年份
        ri = conn.execute("SELECT orig_invoice_no FROM invoice WHERE invoice_no=?", (red_no,)).fetchone()
        orig_year = None
        if ri and ri["orig_invoice_no"]:
            oi = conn.execute("SELECT invoice_date FROM invoice WHERE invoice_no=?", (ri["orig_invoice_no"],)).fetchone()
            if oi:
                orig_year = _year_of(oi["invoice_date"])
        for cd in cds:
            name = cd["person_name"]
            if person and name != person:
                continue
            share = ref["refund_amount"] * abs(cd["billing_amount"]) / total_abs
            st = result.setdefault(name, _new_st(conn, name))
            m = st["months"][refund_month]
            if orig_year is not None and orig_year < year:
                m["rec_refund_prev"] -= share     # ⑤退上年（负数）
            else:
                m["rec_refund_cur"] -= share      # ④退本年（负数）

    # ============ 业务收入 ============
    for name, st in result.items():
        for mo in MONTHS:
            m = st["months"][mo]
            rec_net = (m["rec_open_cur"] + m["rec_cur_year"] + m["rec_prev_year"]
                       + m["rec_refund_cur"] + m["rec_refund_prev"])   # 二小计（净）
            inv_net = (m["inv_open_received"] + m["inv_open_uncollected"]
                       + m["inv_red_cur"] + m["inv_red_prev"])          # 三小计（净）
            if st["staff_type"] == "合伙":
                m["income"] = round(m["inv_total"], 2)   # 本月开票净额（含预收票）
            elif st["staff_type"] in ("聘用", "兼职"):
                m["income"] = round(rec_net, 2)
            else:
                m["income"] = 0.0
        # 本年累计未收 = 本年开票净额 − 本年收款净额（模板口径）
        inv_total = sum(st["months"][mo]["inv_open_received"] + st["months"][mo]["inv_open_uncollected"]
                        + st["months"][mo]["inv_red_cur"] + st["months"][mo]["inv_red_prev"] for mo in MONTHS)
        rec_total = sum(st["months"][mo]["rec_open_cur"] + st["months"][mo]["rec_cur_year"]
                        + st["months"][mo]["rec_prev_year"] + st["months"][mo]["rec_refund_cur"]
                        + st["months"][mo]["rec_refund_prev"] for mo in MONTHS)
        st["uncollected_total"] = round(st["_uncollected_acc"], 2)

    # ============ 费用（六，逐类）============
    exp_rows = conn.execute(
        "SELECT period, actual_handler, expense_type, expense_amount FROM expense_ledger"
    ).fetchall() if not person_filter else conn.execute(
        "SELECT period, actual_handler, expense_type, expense_amount FROM expense_ledger WHERE actual_handler=?",
        person_filter,
    ).fetchall()
    for er in exp_rows:
        name = er["actual_handler"]
        if not name:
            continue
        exp_month = _month_of(er["period"])
        if er["period"] and _year_of(er["period"]) != year:
            continue
        etype = er["expense_type"] or "其他费用"
        st = result.setdefault(name, {"staff_type": _staff_type(conn, name),
                                      "months": {m: _empty_month() for m in MONTHS},
                                      "uncollected_month": {m: 0.0 for m in MONTHS},
                                      "uncollected_total": 0.0,
                                      "expenses": {}})
        if etype not in st["expenses"]:
            st["expenses"][etype] = {m: 0.0 for m in MONTHS}
        st["expenses"][etype][exp_month] += er["expense_amount"] or 0.0

    return result


def _allocated_total(remaining: Dict[str, float], cds, name: str, receipts) -> List[float]:
    """模拟分摊，返回某经办人逐笔收到的金额列表（用于计算该票已收）"""
    rem = {cd["person_name"]: cd["billing_amount"] for cd in cds}
    out = []
    for rec in receipts:
        got = allocate_receipt(rem, rec["amount"])
        out.append(got.get(name, 0.0))
    return out
