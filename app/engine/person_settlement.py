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
- 四、未收款金额：各月列=本月未收（仅蓝字，=⑦，不涉及红冲/往期发票）；合计列=累计未收（截止该月、含红冲与退款冲减）= Σ(⑦本月未收 + ⑧红冲本年 + ⑨红冲上年 − ②收本年 − ③收上年 − ④退本年 − ⑤退上年)；其中 ①本月开收=⑥本月开收 互相抵消。即：累计未收 = Σ(三·本月开具发票金额 − 二·收款净额)，红冲/退款均冲减累计未收。
- 五、业务收入：合伙=本月开票净额；聘用/兼职=本月收款净额；其他类型=0
- 六、减：分成报酬及费用：按费用类型逐类列出、按月归属（按导入账期）
"""
from __future__ import annotations

from typing import Dict, List

from app.db import get_conn
from app.engine import staff_type
from app.engine.split import allocate_receipt

MONTHS = list(range(1, 13))

STAFF_TYPE_MAP = {"合伙": "partner", "聘用": "employee", "兼职": "parttime", "其他": "other"}


def _ym(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _ym_parts(ym) -> Tuple[int, int]:
    """健壮解析日期/账期串 → (year, month)（P1-5）。

    - 成功：返回合法 (年, 1..12月)；正常路径与旧 _year_of/_month_of 返回值逐值一致；
    - 失败/越界/空：返回 (0, 0)（两者同生共死：要么都合法，要么都是 0）。
    先走旧实现的快路径（split("-")），失败再经 normalize_date 归一
    （2025/09/01、2025.9、中文年月日、Excel 序列号…），仍失败才判 (0, 0)。
    """
    if not ym:
        return 0, 0
    s = str(ym).strip()
    if not s:
        return 0, 0
    try:
        parts = s.split("-")
        if len(parts) >= 2:
            y, mo = int(parts[0]), int(parts[1])
            if y > 0 and 1 <= mo <= 12:
                return y, mo
    except (ValueError, IndexError):
        pass
    try:
        from app.importer.date_utils import normalize_date  # 延迟导入避免环
        nd = normalize_date(s)
    except Exception:  # noqa: BLE001  彻底解析不了 → (0,0)，由调用方记警告
        return 0, 0
    if nd:
        try:
            y, mo = int(str(nd)[:4]), int(str(nd)[5:7])
            if y > 0 and 1 <= mo <= 12:
                return y, mo
        except ValueError:
            pass
    return 0, 0


def _warn(warnings, msg: str) -> None:
    """P1-5：脏日期必须可见——警告列表由结算页显示，绝不允许静默跳过。"""
    if warnings is not None:
        warnings.append(msg)


def _month_of(ym: str) -> int:
    """月份（1..12）；空/无法解析/越界 → 0（P1-5 健壮化，正常路径不变）。"""
    return _ym_parts(ym)[1]


def _year_of(ym: str) -> int:
    """年份；空/无法解析/越界 → 0（P1-5 健壮化，正常路径不变，不再出现静默 20250105）。"""
    return _ym_parts(ym)[0]


def _staff_type(conn, name: str, override: str | None = None) -> str:
    if override:
        return override
    return _staff_type_orig(conn, name)


def _staff_type_orig(conn, name: str) -> str:
    # 不过滤 is_active：离职人员的历史年份业务仍须按真实身份计（否则「其他」→ 收入 0）
    r = conn.execute("SELECT staff_type FROM staff WHERE name=?", (name,)).fetchone()
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


def build_settlement(year: int, person: str | None = None, person_type: str | None = None,
                     warnings: List[str] | None = None) -> Dict:
    """计算个人结算总表数据

    person_type: 按身份过滤（合伙/聘用/兼职，None=全部=汇总口径）
    warnings: 可选列表（原地追加）。P1-5：日期无法解析的记录不计入结算，但
              逐条写入此处（含发票号/人员/原始值），由结算页显示，不静默。
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
        return _compute(conn, year, person, person_type, warnings)
    finally:
        conn.close()


def build_settlement_conn(conn, year: int, person: str | None = None,
                          person_type: str | None = None,
                          warnings: List[str] | None = None) -> Dict:
    """同 build_settlement，但使用调用方提供的连接（供分成计算引擎等复用口径，不自行开连接）。"""
    return _compute(conn, year, person, person_type, warnings)


def _new_st(conn, name: str, override: str | None = None) -> Dict:
    """标准人员结构（override=身份，优先于人员类型）"""
    return {
        "staff_type": _staff_type(conn, name, override),
        "months": {m: _empty_month() for m in MONTHS},
        "uncollected_month": {m: 0.0 for m in MONTHS},
        "uncollected_total": 0.0,
        "expenses": {},
    }


def _empty_month() -> Dict[str, float]:
    return {k: 0.0 for k in (
        "rec_open_cur", "rec_cur_year", "rec_prev_year", "rec_refund_cur", "rec_refund_prev",
        "inv_open_received", "inv_open_uncollected", "inv_red_cur", "inv_red_prev",
        "inv_total", "income",
    )}


def cumulative_uncollected(st: dict, mo: int = 0) -> float:
    """累计未收（截止 mo 月，mo=0 表示全年）。

    = Σ_{1..mo}(三·本月开具发票金额 − 收款净额)
    = Σ(⑦本月未收 + ⑧红冲本年 + ⑨红冲上年 − ②收本年 − ③收上年 − ④退本年 − ⑤退上年)
    其中 ①本月开收 == ⑥本月开收 互相抵消，故不出现。红冲/退款均冲减累计未收。
    """
    months = range(1, 13) if not mo else range(1, mo + 1)
    return round(sum(
        (st["months"][m]["inv_open_received"] + st["months"][m]["inv_open_uncollected"]
         + st["months"][m]["inv_red_cur"] + st["months"][m]["inv_red_prev"])
        - (st["months"][m]["rec_open_cur"] + st["months"][m]["rec_cur_year"]
           + st["months"][m]["rec_prev_year"] + st["months"][m]["rec_refund_cur"]
           + st["months"][m]["rec_refund_prev"])
        for m in months), 2)


def _compute(conn, year: int, person: str | None, person_type: str | None = None,
             warnings: List[str] | None = None) -> Dict:
    result: Dict[str, Dict] = {}
    person_filter = (person,) if person else None
    pt_filter = (" AND person_type=?" if person_type else "")

    # ============ 收款分摊（按经办人）============
    # 发票维度：开票信息 + 收款记录（正数发票才有收款分摊）
    _inv_where, _inv_params = "1=1", []
    if person:
        _inv_where += """ AND EXISTS (SELECT 1 FROM charge_detail cd
                       WHERE cd.invoice_no = i.invoice_no AND cd.person_name = ?)"""
        _inv_params.append(person)
    if person_type:
        _inv_where += """ AND EXISTS (SELECT 1 FROM charge_detail cd
                       WHERE cd.invoice_no = i.invoice_no AND cd.person_type = ?)"""
        _inv_params.append(person_type)
    invoices = conn.execute(
        f"""SELECT i.invoice_no, i.invoice_date, i.total_amount, i.orig_invoice_no
            FROM invoice i WHERE i.total_amount >= 0 AND {_inv_where}
            ORDER BY i.invoice_date, i.invoice_no""", _inv_params
    ).fetchall()

    receipts_by_inv: Dict[str, List] = {}
    # ---- 红字发票查询 + 红冲映射（未收冲减用）----
    _rd_where, _rd_params = "1=1", []
    if person:
        _rd_where += """ AND EXISTS (SELECT 1 FROM charge_detail cd
                       WHERE cd.invoice_no = i.invoice_no AND cd.person_name = ?)"""
        _rd_params.append(person)
    if person_type:
        _rd_where += """ AND EXISTS (SELECT 1 FROM charge_detail cd
                       WHERE cd.invoice_no = i.invoice_no AND cd.person_type = ?)"""
        _rd_params.append(person_type)
    reds = conn.execute(
        f"""SELECT i.invoice_no, i.invoice_date, i.total_amount, i.orig_invoice_no
            FROM invoice i WHERE i.total_amount < 0 AND {_rd_where}
            ORDER BY i.invoice_date""", _rd_params
    ).fetchall()
    # P0-1 修复：红冲单一出口 —— 蓝票侧不再建 red_by_orig 映射去扣减蓝票
    # （红字统一在下方「红字发票」循环计入 ⑧⑨，消除双重扣减 / 多红字覆盖 / 同月蒸发）。

    for inv in invoices:
        receipts_by_inv[inv["invoice_no"]] = conn.execute(
            "SELECT amount, receipt_date, person_name FROM collection WHERE invoice_no=? ORDER BY id",
            (inv["invoice_no"],),
        ).fetchall()

    # 经办人开票拆分
    cds_by_inv: Dict[str, List] = {}
    for inv in invoices:
        cds_by_inv[inv["invoice_no"]] = conn.execute(
            "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=?" + pt_filter + " ORDER BY id",
            (inv["invoice_no"],) + ((person_type,) if person_type else ()),
        ).fetchall()

    for inv in invoices:
        no = inv["invoice_no"]
        inv_year, inv_month = _ym_parts(inv["invoice_date"])
        if inv_year == 0 and inv["invoice_date"]:
            # P1-5：脏开票日期（旧代码在此 IndexError）→ 显式警告 + 整票跳过，不静默
            _warn(warnings, f"发票 {no} 开票日期无法解析：「{inv['invoice_date']}」，"
                            f"该票本年不计入开票/收款统计")
            continue
        cds = cds_by_inv.get(no, [])
        if not cds:
            continue
        # 每人开票份额
        remaining = {cd["person_name"]: cd["billing_amount"] for cd in cds}
        # 逐笔收款分摊
        for rec in receipts_by_inv.get(no, []):
            amount, rec_date = rec["amount"], rec["receipt_date"]
            pname = rec["person_name"]
            if pname and pname in remaining:
                # 已按经办人归因的收款：直接计入该经办人并扣减其应收池
                # P3-2：负金额（回写弹窗手输/异常数据）只按 |amount| 冲减其应收池、
                # 不计入收款（对称逻辑）——旧行为 `remaining - amount` 等价于给池加钱，
                # 会让其后的未归因收款多分给该人（收款合计/收入虚增、超收判定失效）。
                got = {pname: amount} if amount > 0 else {}
                remaining[pname] = max(remaining[pname] - abs(amount), 0.0)
                if amount < 0:
                    _warn(warnings, f"发票 {no} 出现负金额收款（{amount}，经办 {pname}），"
                                    f"已按冲减处理：不计入收款、只冲减其应收份额")
            else:
                # 未归因（历史/普通导入）：按开票份额比例分摊兜底
                got = allocate_receipt(remaining, amount)
            rec_year, rec_month = _ym_parts(rec_date)
            if rec_year == 0 and rec_date:
                # P1-5：脏收款日期（旧代码 IndexError）→ 显式警告；空日期维持旧版静默跳过
                _warn(warnings, f"发票 {no} 收款日期无法解析：「{rec_date}」"
                                f"（金额 {amount}，经办 {pname or '未归因'}），本笔不计入")
            if rec_year != year:
                continue  # 只统计本年收款（含解析失败/空日期）
            for name, val in got.items():
                if val == 0:
                    continue
                st = result.setdefault(name, _new_st(conn, name, person_type))
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
            st = result.setdefault(name, _new_st(conn, name, person_type))
            m = st["months"][inv_month]
            # 已收部分（该经办人在此票上的分摊累计已收，含期外预收）
            got_total = sum(_allocated_total(remaining, cds, name, receipts_by_inv.get(no, [])))
            # P0-1 修复：红字唯一出口在下方红票循环；蓝票侧不再扣减红冲
            billing_eff = billing
            uncollected = round(billing_eff - got_total, 2)
            if uncollected > 0.01:
                m["inv_open_uncollected"] += uncollected   # ⑦本月未收
                st["uncollected_month"][inv_month] += uncollected  # 四·本月
            # ①/⑥本月开收 = 本月开票且已收款（含当月收款与以前月份预收款）
            received = round(billing_eff - max(uncollected, 0.0), 2)
            m["rec_open_cur"] += received          # ①
            m["inv_open_received"] += received     # ⑥
            # 本月开票总额（三小计独立计算，含预收票）
            m["inv_total"] += billing

    # ============ 红字发票（三⑧⑨）============
    for red in reds:
        red_year, red_month = _ym_parts(red["invoice_date"])
        if red_year == 0 and red["invoice_date"]:
            # P1-5：脏红冲日期（旧代码 IndexError）→ 显式警告 + 跳过
            _warn(warnings, f"红字发票 {red['invoice_no']} 日期无法解析：「{red['invoice_date']}」，"
                            f"本年不计入红冲")
            continue
        if red_year != year:
            continue  # 只统计本年红冲
        cds = conn.execute(
            "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=?" + pt_filter + " ORDER BY id",
            (red["invoice_no"],) + ((person_type,) if person_type else ()),
        ).fetchall()
        if not cds:
            continue
        # 原票年份/月份
        orig_year, orig_month = None, None
        if red["orig_invoice_no"]:
            oi = conn.execute("SELECT invoice_date FROM invoice WHERE invoice_no=?", (red["orig_invoice_no"],)).fetchone()
            if oi:
                oy, om = _ym_parts(oi["invoice_date"])
                if oy == 0 and oi["invoice_date"]:
                    # P1-5：脏原票日期 → 警告并按「原票缺失」既有口径归本年红冲
                    _warn(warnings, f"红字发票 {red['invoice_no']} 原票 {red['orig_invoice_no']} "
                                    f"日期无法解析：「{oi['invoice_date']}」，按原票缺失归本年红冲")
                else:
                    orig_year, orig_month = oy, om
        for cd in cds:
            name = cd["person_name"]
            st = result.setdefault(name, _new_st(conn, name, person_type))
            m = st["months"][red_month]
            val = cd["billing_amount"]  # 负数
            m["inv_total"] += val            # 三小计含红冲（红字唯一出口）
            if orig_year is not None and orig_year < year:
                m["inv_red_prev"] += val     # ⑨红冲上年（原票跨年）
            else:
                m["inv_red_cur"] += val      # ⑧红冲本年（原票本年，含同月；原票缺失也归本年）

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
            "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=?" + pt_filter + " ORDER BY id",
            (red_no,) + ((person_type,) if person_type else ()),
        ).fetchall()
        if not cds:
            continue
        total_abs = sum(abs(cd["billing_amount"]) for cd in cds) or 1.0
        refund_year, refund_month = _ym_parts(ref["refund_date"])
        if refund_year == 0 and ref["refund_date"]:
            # P1-5：脏退款日期 → 显式警告
            _warn(warnings, f"退款（红字 {red_no}，金额 {ref['refund_amount']}）"
                            f"日期无法解析：「{ref['refund_date']}」，本笔不计入")
        if refund_year != year:
            continue  # 只统计本年退款（含解析失败/空日期）
        # 原票年份
        ri = conn.execute("SELECT orig_invoice_no FROM invoice WHERE invoice_no=?", (red_no,)).fetchone()
        orig_year = None
        if ri and ri["orig_invoice_no"]:
            oi = conn.execute("SELECT invoice_date FROM invoice WHERE invoice_no=?", (ri["orig_invoice_no"],)).fetchone()
            if oi:
                oy, om = _ym_parts(oi["invoice_date"])
                if oy == 0 and oi["invoice_date"]:
                    # P1-5：脏原票日期 → 警告并按「原票缺失」既有口径归本年退款
                    _warn(warnings, f"退款原票 {ri['orig_invoice_no']} 日期无法解析：「{oi['invoice_date']}」，"
                                    f"按原票缺失归本年退款")
                else:
                    orig_year = oy
        for cd in cds:
            name = cd["person_name"]
            if person and name != person:
                continue
            share = ref["refund_amount"] * abs(cd["billing_amount"]) / total_abs
            st = result.setdefault(name, _new_st(conn, name, person_type))
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
            # 业务收入口径：参与结算由 staff_type_def.is_settle 控制，
            # 净额口径由 net_basis 控制（合伙=开票净额，聘用/兼职=收款净额，其余=0）。
            is_settle, net_basis = staff_type.settle_flags_of(st["staff_type"], conn)
            if is_settle:
                m["income"] = round(m["inv_total"], 2) if net_basis == "开票净额" else round(rec_net, 2)
            else:
                m["income"] = 0.0
            # 方案 D：三小计恒等式硬断言 父(inv_total) == ⑥+⑦+⑧+⑨（P0-1 防回归）
            _ident = (m["inv_open_received"] + m["inv_open_uncollected"]
                      + m["inv_red_cur"] + m["inv_red_prev"])
            if abs(m["inv_total"] - _ident) > 0.02:
                raise AssertionError(
                    f"结算恒等式破坏 {name} {year}-{mo:02d}: "
                    f"inv_total={m['inv_total']:g} 但 ⑥+⑦+⑧+⑨={_ident:g}"
                )
        # 本年累计未收 = Σ(三·本月开具发票金额 − 收款净额)
        #   = Σ(⑦本月未收 + ⑧红冲本年 + ⑨红冲上年 − ②收本年 − ③收上年 − ④退本年 − ⑤退上年)
        #   其中 ①本月开收 == ⑥本月开收 互相抵消，故不出现。红冲/退款均冲减累计未收。
        st["uncollected_total"] = cumulative_uncollected(st, 0)

    # ============ 费用（六，逐类）============
    exp_rows = conn.execute(
        "SELECT period, actual_handler, expense_type, expense_amount FROM expense_ledger"
        + (" WHERE person_type=?" if person_type else ""),
        ((person_type,) if person_type else ()),
    ).fetchall() if not person_filter else conn.execute(
        "SELECT period, actual_handler, expense_type, expense_amount FROM expense_ledger WHERE actual_handler=?"
        + (" AND person_type=?" if person_type else ""),
        person_filter + ((person_type,) if person_type else ()),
    ).fetchall()
    for er in exp_rows:
        name = er["actual_handler"]
        if not name:
            continue
        exp_year, exp_month = _ym_parts(er["period"])
        if exp_year == 0:
            # P1-5：空/脏账期（旧代码 KeyError months[0]）→ 显式警告 + 跳过，不静默
            _warn(warnings, f"费用台账「{name}」账期无法解析：「{er['period']}」"
                            f"（{er['expense_type']} {er['expense_amount'] or 0}），本行不计入费用")
            continue
        if exp_year != year:
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
        pname = rec["person_name"]
        if pname and pname in rem:
            got = {pname: rec["amount"]} if rec["amount"] > 0 else {}
            # P3-2：与 _compute 主循环同款对称逻辑（负金额只冲减、不加池）
            rem[pname] = max(rem[pname] - abs(rec["amount"]), 0.0)
        else:
            got = allocate_receipt(rem, rec["amount"])
        out.append(got.get(name, 0.0))
    return out
