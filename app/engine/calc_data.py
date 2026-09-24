"""分成计算引擎 — 内置指标取数层（DATA() 函数的数据源）

spec: lawfirm_app/calc_engine_spec.md §2.3 / §11

口径总则（复用现有引擎，不另写一套）：
- 结算类指标（业务收入/收款净额/开票净额/未收款/已收/红冲/退款）
  全部取自 person_settlement.build_settlement_conn 的逐月结果，
  与「个人结算总表」页面数字严格一致：
    二 本月收款金额   = ①开收 + ②收本年 + ③收上年 + ④退本年 + ⑤退上年（退为负）
    三 本月开具发票额 = ⑥开收 + ⑦未收 + ⑧红冲本年 + ⑨红冲上年（红冲为负）
    四 未收款金额     = 本月未收；全年 = 本年累计未收
    五 业务收入       = 合伙=本月开票净额；聘用/兼职=本月收款净额；其他=0
- 工资类指标取自 raw_salary（工资累计口径，同 salary_summary.GROSS_EXPR）：
    每月工资 = share_num + salary_num + partner_num（同一月多条都计入）
- 费用合计取自 expense_ledger（actual_handler + 账期）。
- 开票金额 = 正数发票的经办人开票金额合计；红冲金额 = 当月红字发票经办人份额绝对值
  （直接查 charge_detail+invoice；含当月开蓝又当月红冲的票，故可能与结算表⑧+⑨略有差异——
  结算表把同月红冲折进①⑥⑦，此处按"当月红冲额"独立列示）。
- 月省略（None）= 全年累计；未收款金额全年用 uncollected_total（存量累计口径）。
- 全部只读，绝不回写主数据（spec §11.1）。
- 请求级缓存：同一 CalcData 实例内 (职工,指标,年,月) 只算一次；
  结算结果按 (年,职工) 缓存。重开表/新实例自然刷新。
"""
from __future__ import annotations

from typing import Dict, Optional

from app.db import get_conn
from app.engine import person_settlement as _ps
from app.engine.calc_formula import CalcDataFailure

# 指标注册表：name -> 口径说明（UI 下拉/文档共用；数据驱动，加指标=加一条）
BUILTIN_INDICATORS: Dict[str, str] = {
    "开票金额":   "正数发票的经办人开票金额合计（不含红字票）",
    "开票净额":   "个人结算总表·三小计（含红冲、含预收票）",
    "已收金额":   "个人结算总表·一+二+三（本月收款，不含退款）",
    "收款净额":   "个人结算总表·二小计（①~⑤，退为负）",
    "已收净额":   "同「收款净额」",
    "红冲金额":   "个人结算总表·⑧+⑨ 的绝对值（正数表示红冲额）",
    "退款金额":   "个人结算总表·④+⑤ 的绝对值（正数表示退款额）",
    "业务收入":   "个人结算总表·五（合伙=开票净额；聘用/兼职=收款净额）——分成计算主要基数",
    "未收款金额": "个人结算总表·四（月=本月未收；全年=本年累计未收）",
    "每月工资":   "工资表 应发合计（分成报酬+工资+预发经营所得）",
    "代扣个税":   "工资表 代扣个所税",
    "公积金":     "工资表 代扣公积金",
    "实发金额":   "工资表 实发金额",
    "费用合计":   "费用台账 该经办人当期费用总额",
}

# 别名 → 标准名
_ALIASES = {"已收净额": "收款净额"}

MONTHS = list(range(1, 13))


class CalcDataError(CalcDataFailure):
    """DATA() 取数失败（由公式引擎转成对应错误值）。

    继承 `calc_formula.CalcDataFailure` 这个标记基类 → 引擎认得出它是**业务错误**，
    呈现 `#REF!`；而到不了这里的崩溃（表结构缺失等）会走 `#SYSERR!` + 告警。
    """

    unexpected = False


class CalcDataUnexpectedError(CalcDataError):
    """取数过程中冒出的**非预期**异常（sqlite3.Error / 类型错误 / 其它崩溃）。

    由下面的 `_wrap_unexpected()` 统一包一层：带上 职工/指标/账期 上下文，
    并把 `__cause__` 指回原始异常，便于排障。
    """

    unexpected = True


class AmbiguousPersonError(CalcDataError):
    """职工姓名重名，无法唯一定位。"""


class UnknownIndicatorError(CalcDataError):
    """指标名不存在（既非内置也非自定义）。"""


def _wrap_unexpected(exc: BaseException, ctx: str) -> CalcDataUnexpectedError:
    """把非 CalcDataError 的崩溃包成 CalcDataUnexpectedError（保留 __cause__）。"""
    err = CalcDataUnexpectedError(f"{ctx} | {type(exc).__name__}: {exc}")
    err.__cause__ = exc
    return err


def builtin_names() -> list:
    """内置指标名清单（供选择器下拉）。"""
    return list(BUILTIN_INDICATORS.keys())


class CalcData:
    """DATA() 取数器：一个求值批次共用一个实例（享受请求级缓存）。"""

    def __init__(self, conn=None):
        self._own = conn is None
        self.conn = conn if conn is not None else get_conn()
        self._cache: Dict[tuple, float] = {}
        self._settle: Dict[tuple, dict] = {}
        self._ambig: Dict[str, bool] = {}

    # ---------- 对外主入口 ----------
    def get(self, person: str, indicator: str, year: int, month: Optional[int] = None) -> float:
        """取 职工+指标+账期 的数值。month=None 表示全年累计。"""
        person = (person or "").strip()
        indicator = (indicator or "").strip()
        indicator = _ALIASES.get(indicator, indicator)
        if indicator not in BUILTIN_INDICATORS:
            raise UnknownIndicatorError(f"未知指标：{indicator}")
        if month is not None and month not in MONTHS:
            raise CalcDataError(f"月份非法：{month}")
        if self.is_ambiguous(person):
            raise AmbiguousPersonError(f"职工姓名重名：{person}")
        key = (person, indicator, int(year), month)
        if key not in self._cache:
            ctx = f"职工={person!r} 指标={indicator!r} 年={year} 月={month}"
            try:
                value = self._compute(person, indicator, int(year), month)
            except CalcDataError:      # 业务错误（未知指标/重名/非法月份）原样上抛
                raise
            except Exception as e:     # 非预期：表结构缺失、驱动错误、后续 RP bug
                raise _wrap_unexpected(e, ctx) from e
            self._cache[key] = round(value, 2)
        return self._cache[key]

    # ---------- 重名检测 ----------
    def is_ambiguous(self, person: str) -> bool:
        """staff 表同名>1 视为歧义（不区分在职状态，离职人员仍可取数）。"""
        person = (person or "").strip()
        if not person:
            return False
        if person not in self._ambig:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM staff WHERE name=?", (person,)).fetchone()
            self._ambig[person] = bool(row and row["n"] > 1)
        return self._ambig[person]

    # ---------- 结算结果（按 年+职工 缓存）----------
    def _settlement(self, person: str, year: int) -> dict:
        key = (year, person)
        if key not in self._settle:
            self._settle[key] = _ps.build_settlement_conn(self.conn, year, person)
        return self._settle[key].get(person) or {}

    @staticmethod
    def _month_val(st: dict, key: str, month: Optional[int]) -> float:
        """取某人某指标：月=当月值；None=12 个月求和。无数据返回 0。"""
        if not st:
            return 0.0
        months = st.get("months", {})
        if month is not None:
            return float(months.get(month, {}).get(key, 0.0))
        return float(sum(months.get(m, {}).get(key, 0.0) for m in MONTHS))

    # ---------- 各指标计算 ----------
    def _compute(self, person: str, indicator: str, year: int, month: Optional[int]) -> float:
        if indicator in ("开票金额", "红冲金额"):
            return self._invoice_amount(person, year, month,
                                        positive=(indicator == "开票金额"))
        if indicator == "费用合计":
            return self._expense_total(person, year, month)
        if indicator in ("每月工资", "代扣个税", "公积金", "实发金额"):
            return self._salary(person, indicator, year, month)

        # ---- 结算类（复用 person_settlement 口径）----
        st = self._settlement(person, year)
        if indicator == "业务收入":
            return self._month_val(st, "income", month)
        if indicator in ("收款净额", "已收净额"):
            return (self._month_val(st, "rec_open_cur", month)
                    + self._month_val(st, "rec_cur_year", month)
                    + self._month_val(st, "rec_prev_year", month)
                    + self._month_val(st, "rec_refund_cur", month)
                    + self._month_val(st, "rec_refund_prev", month))
        if indicator == "已收金额":
            return (self._month_val(st, "rec_open_cur", month)
                    + self._month_val(st, "rec_cur_year", month)
                    + self._month_val(st, "rec_prev_year", month))
        if indicator == "开票净额":
            return self._month_val(st, "inv_total", month)
        if indicator == "红冲金额":
            return abs(self._month_val(st, "inv_red_cur", month)
                       + self._month_val(st, "inv_red_prev", month))
        if indicator == "退款金额":
            return abs(self._month_val(st, "rec_refund_cur", month)
                       + self._month_val(st, "rec_refund_prev", month))
        if indicator == "未收款金额":
            if month is not None:
                return float((st.get("uncollected_month") or {}).get(month, 0.0))
            return float(st.get("uncollected_total") or 0.0)
        raise UnknownIndicatorError(f"未知指标：{indicator}")

    # ---------- 发票类 ----------
    def _invoice_amount(self, person: str, year: int, month: Optional[int],
                        positive: bool) -> float:
        sign = ">=" if positive else "<"
        date_filter = ("strftime('%Y-%m', i.invoice_date) = ?" if month is not None
                       else "strftime('%Y', i.invoice_date) = ?")
        ym = f"{year:04d}-{month:02d}" if month is not None else f"{year:04d}"
        row = self.conn.execute(
            f"""SELECT COALESCE(SUM(cd.billing_amount),0) AS v
                FROM charge_detail cd JOIN invoice i ON cd.invoice_no = i.invoice_no
                WHERE cd.person_name = ? AND i.total_amount {sign} 0 AND {date_filter}""",
            (person, ym),
        ).fetchone()
        v = float(row["v"] or 0.0)
        # 红冲金额返回正数（红字份额绝对值）；开票金额本身为正
        return abs(v) if not positive else v

    # ---------- 费用 ----------
    def _expense_total(self, person: str, year: int, month: Optional[int]) -> float:
        date_filter = ("substr(period,1,7) = ?" if month is not None
                       else "substr(period,1,4) = ?")
        ym = f"{year:04d}-{month:02d}" if month is not None else f"{year:04d}"
        row = self.conn.execute(
            f"""SELECT COALESCE(SUM(expense_amount),0) AS v
                FROM expense_ledger
                WHERE actual_handler = ? AND IFNULL(period,'') != '' AND {date_filter}""",
            (person, ym),
        ).fetchone()
        return float(row["v"] or 0.0)

    # ---------- 工资（口径同 salary_summary.GROSS_EXPR）----------
    _SAL_COL = {"每月工资": "(COALESCE(s.share_num,0) + COALESCE(s.salary_num,0) + COALESCE(s.partner_num,0))",
                "代扣个税": "COALESCE(s.tax_num,0)",
                "公积金":   "COALESCE(s.fund_num,0)",
                "实发金额": "COALESCE(s.net_num,0)"}

    def _salary(self, person: str, indicator: str, year: int, month: Optional[int]) -> float:
        date_filter = ("substr(b.period,1,7) = ?" if month is not None
                       else "substr(b.period,1,4) = ?")
        ym = f"{year:04d}-{month:02d}" if month is not None else f"{year:04d}"
        row = self.conn.execute(
            f"""SELECT COALESCE(SUM({self._SAL_COL[indicator]}),0) AS v
                FROM raw_salary s
                LEFT JOIN import_batch b ON b.id = s.import_batch_id
                WHERE s.staff_name = ? AND {date_filter}""",
            (person, ym),
        ).fetchone()
        return float(row["v"] or 0.0)

    def close(self):
        if self._own:
            self.conn.close()
