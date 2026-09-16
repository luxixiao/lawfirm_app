"""数据行身份（person_type）回填与标注

身份默认 = 人员当前类型（staff.staff_type）；少数双身份者可在台账数据页手动改。
"""
from __future__ import annotations

from typing import Iterable, List

from app.db import get_conn

# 非员工经办人白名单（公共费用等）。与导入校验共用同一份，避免两处口径漂移。
HANDLER_WHITELIST = {"公共", "行政"}


def is_period_before(ym: str, period: str) -> bool:
    """开票月份(YYYY-MM) 是否早于导入账期月份 —— 期外口径 a（**已停用，2026-09-16**）。

    ⚠️ 本函数**不再是**「需补录原票」的判定依据。现口径**只看票号**（sheet3 且票号不在库，
    见 `library_invoice_nos` 与 `app.importer.ledger_import.is_deferred_invoice`）：
    同一张历史应收会持续出现在各期 sheet3，按日期判「期外」会反复处理同一张票，
    且开票日期解析失败的行会被静默漏出待补录清单。

    本函数保留两处**历史/校验**用途（本期不删，避免连带）：
    1. A4 数据质量校验：sheet3 行开票月份 ≥ 账期 → 报错中止导入
       （见 `app.importer.ledger_import._parse_invoice_sheet`）；
    2. 测试与历史数据核对。

    "早于"按 YYYY-MM 字符串字典序比较（同年同月格式下等价于时间序）。
    """
    ym = (ym or "").strip()
    period = (period or "").strip()
    return bool(ym) and bool(period) and ym < period


def library_invoice_nos(conn=None) -> set:
    """库中已有发票号码集合 —— 「票号是否在库」的**唯一口径来源**。

    用途（两处必须同源，故收在此处，避免判定漂移）：
    - sheet3（应收账款）行判定「**需补录原票**」（票号不在库）
      还是「**已入库，请确认收款**」（票号在库，D1 甲），见
      `app.importer.ledger_import.is_deferred_invoice` / `split_deferred`；
    - 待补录派生（源 B）`app.engine.raw_ledger.deferred_sheet3_invoices`
      以「invoice 表无该号」为准，与此同源。

    数据量级数千行，全表取号成本可忽略；若将来变慢，再按年份/账期限定。
    conn：传入则复用外部连接（单事务/测试注入用），不传自开自关。
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        return {
            (r[0] or "").strip()
            for r in conn.execute("SELECT invoice_no FROM invoice WHERE invoice_no IS NOT NULL")
        } - {""}
    finally:
        if own:
            conn.close()


def norm_type(staff_type) -> str:
    t = (staff_type or "").strip()
    if "合伙" in t:
        return "合伙"
    if "兼职" in t:
        return "兼职"
    if "聘用" in t:
        return "聘用"
    return "其他"


def all_staff_names(conn) -> set:
    """花名册全部姓名（**不过滤 is_active**，「停用」功能已取消）。"""
    return {r["name"] for r in conn.execute("SELECT name FROM staff")}


def missing_handlers(conn, names: Iterable[str]) -> List[str]:
    """返回「不在花名册、且不在白名单」的经办人（去重 + 升序）。

    导入写前校验与补录保存校验共用本函数，保证两处口径完全一致
    （否则同一个人在一个入口被拦、在另一个入口放行）。
    """
    staff = all_staff_names(conn)
    out = set()
    for raw in names:
        n = (raw or "").strip()
        if n and n not in staff and n not in HANDLER_WHITELIST:
            out.add(n)
    return sorted(out)


def staff_type_of(conn, name: str) -> str:
    """姓名 → 结算身份（合伙/聘用/兼职/其他）。**不过滤 is_active**。

    离职人员仍会有历史业务数据，若按 is_active 过滤会返回 '' → norm_type 落成
    「其他」→ 其结算业务收入被判 0。故一律按花名册取真实类型。
    """
    r = conn.execute("SELECT staff_type FROM staff WHERE name=?", (name,)).fetchone()
    return norm_type(r["staff_type"]) if r else ""


def backfill_person_types() -> None:
    """历史数据回填：person_type 为空的行按人员当前类型标注"""
    conn = get_conn()
    try:
        for name in [r["person_name"] for r in conn.execute(
                "SELECT DISTINCT person_name FROM charge_detail WHERE person_type=''")]:
            t = staff_type_of(conn, name)
            if t:
                conn.execute("UPDATE charge_detail SET person_type=? WHERE person_name=? AND person_type=''",
                             (t, name))
        for name in [r["actual_handler"] for r in conn.execute(
                "SELECT DISTINCT actual_handler FROM expense_ledger WHERE person_type=''")]:
            if not name:
                continue
            t = staff_type_of(conn, name)
            if t:
                conn.execute("UPDATE expense_ledger SET person_type=? WHERE actual_handler=? AND person_type=''",
                             (t, name))
        conn.commit()
    finally:
        conn.close()
