"""calc_formula（公式引擎内核）单元测试 — 纯 Python，FakeProvider，不触库。

运行：python tests/test_calc_formula.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine.calc_formula import (  # noqa: E402
    Engine, CellProvider, ErrVal, classify_cell, a1_to_rc, rc_to_a1, is_err,
    Parser, _parse_ref_part, CalcDataFailure, ERR_UNEXPECTED,
)
from app.engine.calc_ast_render import render


class _BizErr(CalcDataFailure):
    """模拟 calc_data.CalcDataError（业务错误）：没有 unexpected 标记 → 走 #REF!。"""


class FakeProvider(CellProvider):
    def __init__(self, cells=None, params=None, data=None, sheets=("Sheet1", "Sheet2"),
                 data_error=None):
        self.cells = cells or {}        # (sheet, r0, c0) -> raw
        self.params = params or {}      # (sheet, name) -> value
        self.data_map = data or {}      # (person, indicator, year, month) -> value
        self.sheets = set(sheets)
        self.data_error = data_error    # 取数时要抛的异常（测错误分流用）

    def raw_cell(self, sheet, r, c):
        raw = self.cells.get((sheet, r, c), "")
        return raw, classify_cell(raw)

    def param(self, sheet, name):
        return self.params.get((sheet, name))

    def data(self, person, indicator, year, month):
        if self.data_error is not None:
            raise self.data_error
        return self.data_map[(person, indicator, year, month)]

    def has_sheet(self, sheet):
        return sheet in self.sheets


def make_engine(cells=None, params=None, data=None, **kw):
    return Engine(FakeProvider(cells, params, data, **kw), "Sheet1")


def run(src, **kw):
    return make_engine(**kw).evaluate(src)


OK, FAILS = 0, []


def check(label, got, want):
    global OK
    if want is None:
        if got is None:
            OK += 1
        else:
            FAILS.append(f"{label}: got={got!r} want=None")
        return
    if isinstance(want, (tuple, list)):
        if tuple(got) == tuple(want):
            OK += 1
        else:
            FAILS.append(f"{label}: got={got!r} want={want}")
    elif isinstance(want, str) and want.startswith("#"):
        if is_err(got) and got.code == want:
            OK += 1
        else:
            FAILS.append(f"{label}: got={got!r} want={want}")
    elif isinstance(want, str):
        if got == want:
            OK += 1
        else:
            FAILS.append(f"{label}: got={got!r} want={want}")
    elif isinstance(got, ErrVal):
        FAILS.append(f"{label}: got={got!r} want={want}")
    elif abs(float(got) - float(want)) <= 1e-9:
        OK += 1
    else:
        FAILS.append(f"{label}: got={got!r} want={want}")


def main() -> int:
    # ===== 1. 基础运算 / 优先级 / 一元负号 / 幂 =====
    check("1+2*3", run("=1+2*3"), 7)
    check("(1+2)*3", run("=(1+2)*3"), 9)
    check("10/4", run("=10/4"), 2.5)
    check("-5+3", run("=-5+3"), -2)
    check("2^10", run("=2^10"), 1024)
    check("无=前缀", run("1+1"), 2)
    check("0.1+0.2 舍入到2位", run("=ROUND(0.1+0.2,2)"), 0.3)

    # ===== 2. ROUND half-up（非银行家舍入）=====
    check("ROUND(2.675,2)", run("=ROUND(2.675,2)"), 2.68)
    check("ROUND(1.005,2)", run("=ROUND(1.005,2)"), 1.01)
    check("ROUND(-2.5,0)", run("=ROUND(-2.5,0)"), -3)
    check("ROUND(123.456,1)", run("=ROUND(123.456,1)"), 123.5)
    check("ROUND(0)", run("=ROUND(7)"), 7)

    # ===== 3. 单元格 / 空格=0 / 文本语义 =====
    cells = {("Sheet1", 0, 0): "5", ("Sheet1", 0, 1): "", ("Sheet1", 0, 2): "abc"}
    check("A1+B1(空格=0)", run("=A1+B1", cells=cells), 5)
    check("文本+数字→#VALUE!", run("=C1+1", cells=cells), "#VALUE!")
    check("A1*2", run("=A1*2", cells=cells), 10)
    # 数字文本参与运算（Excel 兼容强制转换）
    cells_err = {("Sheet1", 0, 0): "=1/0"}
    check("错误传播", run("=A1+1", cells=cells_err), "#DIV/0!")

    # ===== 4. 聚合函数 =====
    ag = {("Sheet1", r, 0): v for r, v in enumerate(["1", "2", "abc", "", "4"])}
    check("SUM 忽略文本空格", run("=SUM(A1:A5)", cells=ag), 7)
    check("SUM 区域+散值", run("=SUM(A1:A5,10)", cells=ag), 17)
    check("AVERAGE", run("=AVERAGE(A1:A5)", cells=ag), 7 / 3)
    check("MIN", run("=MIN(A1:A5)", cells=ag), 1)
    check("MAX", run("=MAX(A1:A5)", cells=ag), 4)
    check("MIN 空区域=0", run("=MIN(Z1:Z5)", cells=ag), 0)
    check("AVERAGE 空=#DIV/0!", run("=AVERAGE(Z1:Z5)", cells=ag), "#DIV/0!")
    check("区域含错误→传播", run("=SUM(A1:B2)",
          cells={("Sheet1", 0, 1): "=1/0"}), "#DIV/0!")
    check("ABS", run("=ABS(0-3)"), 3)

    # ===== 5. ROUND 精度 / 嵌套 =====
    check("嵌套函数", run("=ROUND(SUM(A1:A2)*2,1)",
          cells={("Sheet1", 0, 0): "1.25", ("Sheet1", 1, 0): "1.3"}), 5.1)

    # ===== 6. IF 惰性 + 比较 =====
    check("IF 真", run("=IF(2>1,10,20)"), 10)
    check("IF 假", run("=IF(2<1,10,20)"), 20)
    check("IF 惰性(假分支除零不触发)", run("=IF(1=1,5,1/0)"), 5)
    check("IF 无第三参", run("=IF(1>2,5)"), 0)
    check("条件引用", run("=IF(A1>=100,1,0)", cells={("Sheet1", 0, 0): "100"}), 1)
    check("<>", run("=IF(1<>2,1,0)"), 1)
    check("文本比较", run('=IF("a"="a",1,2)'), 1)
    check("数字<文本(Excel序)", run("=IF(1<\"a\",1,0)"), 1)

    # ===== 7. 跨表引用 =====
    cs = {("Sheet2", 0, 0): "7", ("Sheet2", 0, 1): "3", ("Sheet2", 1, 0): "1"}
    check("Sheet2!A1*2", run("=Sheet2!A1*2", cells=cs), 14)
    check("跨表区域 SUM", run("=SUM(Sheet2!A1:B2)", cells=cs), 11)
    check("不存在表→#REF!", run("=NoSheet!A1", cells=cs), "#REF!")
    check("跨表区域不存在表", run("=SUM(NoSheet!A1:A2)"), "#REF!")
    check("中文表名引用", _zh_check(), 9)

    # ===== 8. PARAM =====
    pm = {("Sheet1", "提成比例"): 0.3}
    check("PARAM 引用", run('=PARAM("提成比例")*100', params=pm), 30)
    check("PARAM 未定义→#NAME?", run('=PARAM("没有")', params=pm), "#NAME?")
    check("跨表 PARAM", run('=Sheet2!PARAM("rate")',
          params={("Sheet2", "rate"): 0.5}), 0.5)
    check("跨表 PARAM 表不存在", run('=NoX!PARAM("rate")'), "#REF!")

    # ===== 9. DATA =====
    dt = {("周立生", "业务收入", 2025, 1): 1234.5,
          ("周立生", "业务收入", 2025, None): 12000.0}
    check("DATA 月度", run('=DATA("周立生","业务收入",2025,1)', data=dt), 1234.5)
    check("DATA 全年", run('=DATA("周立生","业务收入",2025)', data=dt), 12000)
    check("DATA 全年字样", run('=DATA("周立生","业务收入",2025,"全年")', data=dt), 12000)
    check("DATA 参数取自单元格",
          run('=DATA(A1,"业务收入",2025,1)',
              cells={("Sheet1", 0, 0): "周立生"}, data=dt), 1234.5)
    check("DATA 月份非法", run('=DATA("周立生","业务收入",2025,13)', data=dt), "#VALUE!")
    # --- DATA 错误分流：业务错误 vs 非预期崩溃（2026-09-25 新增）---
    # 以前一视同仁兜成 #REF!，导致「少了一个迁移列」这类致命问题
    # 在网格里看起来只是普通的引用断开（见 _smoke_calc_export 的 DATA 假红事故）。
    check("DATA 业务错误（CalcDataFailure）→#REF!",
          run('=DATA("周立生","业务收入",2025,1)',
              data_error=_BizErr("未知指标：业务收入")), "#REF!")
    syserr = run('=DATA("无名","开票金额",2025,1)')   # KeyError：非业务异常
    check("DATA 非预期崩溃→#SYSERR!", syserr, ERR_UNEXPECTED)
    _msg = getattr(syserr, "msg", "")
    check("SYSERR msg 含职工上下文", "职工='无名'" in _msg, True)
    check("SYSERR msg 含指标上下文", "指标='开票金额'" in _msg, True)
    check("SYSERR msg 含账期上下文", "账期=2025年1月" in _msg, True)
    check("SYSERR msg 含原始异常类型", "KeyError" in _msg, True)

    # ===== 10. 未知函数 / 语法错 / 循环 =====
    check("未知函数→#NAME?", run("=NOSUCH(1)"), "#NAME?")
    check("语法错→#ERROR!", run("=1+"), "#ERROR!")
    check("裸名称→#ERROR!", run("=foo"), "#ERROR!")
    cyc = {("Sheet1", 0, 0): "=B1", ("Sheet1", 0, 1): "=A1"}  # A1↔B1 互指
    eng = make_engine(cyc)
    check("循环引用→#CIRC!", eng.cell_value("Sheet1", 0, 0), "#CIRC!")
    self_ref = {("Sheet1", 0, 0): "=A1+1"}
    check("自引用→#CIRC!", make_engine(self_ref).cell_value("Sheet1", 0, 0), "#CIRC!")

    # ===== 11. 坐标换算 / 分类 =====
    check("a1_to_rc", a1_to_rc("AA12"), (11, 26))
    check("rc_to_a1", rc_to_a1(11, 26), "AA12")
    check("classify formula", classify_cell("=A1+1"), "formula")
    check("classify number", classify_cell("3.14"), "number")
    check("classify text", classify_cell("周立生"), "text")
    check("classify blank", classify_cell(""), "blank")
    check("classify 负数", classify_cell("-5"), "number")

    # ===== 12. 幂运算：负底数 × 非整数指数（P1-1，Excel 语义 #NUM!）=====
    check("负底数开平方→#NUM!", run("=(-8)^0.5"), "#NUM!")
    check("负底数负指数→#NUM!", run("=(-8)^-0.5"), "#NUM!")
    check("负底数1.5次幂→#NUM!", run("=(-8)^1.5"), "#NUM!")
    check("负底数整数幂正常", run("=(-8)^2"), 64.0)
    check("负底数整数幂-3次方", run("=(-2)^-3"), -0.125)
    check("正底数开方正常", run("=8^0.5"), 2.8284271247461903)
    check("溢出→#VALUE!", run("=2^10000"), "#VALUE!")
    check("0^-1→#VALUE!", run("=0^-1"), "#VALUE!")
    check("单元格负值开方→#NUM!", run("=A1^0.5", cells={("Sheet1", 0, 0): "-8"}), "#NUM!")

    # ===== 13. 绝对引用解析与渲染（G0-4 完善项：与 spec §2.2 ref 分支对齐）=====
    # 解析+渲染 round-trip：四种 $ 组合必须保持原样
    for _s in ("$A$1", "$A1", "A$1", "A1"):
        check(f"G0-4 绝对引用 round-trip {_s}", render(Parser(_s).parse()), _s)
    # 混合绝对符区域
    check("G0-4 区域 round-trip $A$1:B$2", render(Parser("$A$1:B$2").parse()), "$A$1:B$2")
    check("G0-4 区域 round-trip A$1:$B2", render(Parser("A$1:$B2").parse()), "A$1:$B2")
    # _parse_ref_part 直接断言 abs 标志位（row0, col0, abs_row, abs_col）
    check("G0-4 _parse_ref_part $A$1", _parse_ref_part("$A$1"), (0, 0, True, True))
    check("G0-4 _parse_ref_part $A1", _parse_ref_part("$A1"), (0, 0, False, True))
    check("G0-4 _parse_ref_part A$1", _parse_ref_part("A$1"), (0, 0, True, False))
    check("G0-4 _parse_ref_part A1", _parse_ref_part("A1"), (0, 0, False, False))
    check("G0-4 _parse_ref_part 非引用→None", _parse_ref_part("ABC"), None)
    check("G0-4 _parse_ref_part 列后杂字→None", _parse_ref_part("A1B2"), None)

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


def _zh_check():
    eng = Engine(FakeProvider(cells={("分成表", 0, 0): "9"},
                              sheets=("Sheet1", "分成表")), "Sheet1")
    return eng.evaluate("=分成表!A1")


if __name__ == "__main__":
    sys.exit(main())
