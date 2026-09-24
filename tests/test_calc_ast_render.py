"""calc_ast_render（AST→文本反向序列化器）单元测试 — 纯 Python。

运行：python tests/test_calc_ast_render.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine.calc_formula import Parser   # noqa: E402
from app.engine.calc_ast_render import render  # noqa: E402


OK, FAILS = 0, []


def check(label, got, want):
    global OK
    if got == want:
        OK += 1
    else:
        FAILS.append(f"{label}: got={got!r} want={want!r}")


def r(formula: str) -> str:
    """parse(去 '=') → render。"""
    body = formula[1:] if formula.startswith("=") else formula
    return render(Parser(body).parse())


def main() -> int:
    # ===== 1. parse→render 闭环（规范形）=====
    check("A1+B1", r("=A1+B1"), "A1+B1")
    check("SUM range", r("=SUM(A1:A10)"), "SUM(A1:A10)")
    check("跨表绝对混合 range", r("=Sheet2!$A$1:B$2"), "Sheet2!$A$1:B$2")
    check("DATA*PARAM(中文)", r('=DATA("周","业务收入",2025,1)*PARAM("提成比例")'),
          'DATA("周","业务收入",2025,1)*PARAM("提成比例")')
    check("一元负号作用二元→加括号", r("=-(A1+B1)"), "-(A1+B1)")
    check("IF", r("=IF(A1>=100,1,0)"), "IF(A1>=100,1,0)")

    # ===== 2. 优先级 / 最小括号 =====
    check("优先级 1+2*3", r("=1+2*3"), "1+2*3")
    check("(1+2)*3", r("=(1+2)*3"), "(1+2)*3")
    check("A1-(B1+C1)", r("=A1-(B1+C1)"), "A1-(B1+C1)")
    check("^ 右结合", r("=2^3^2"), "2^3^2")
    check("比较最低优先级", r("=A1>B1+C1"), "A1>B1+C1")
    check("嵌套函数", r("=ROUND(SUM(A1:A2)*2,1)"), "ROUND(SUM(A1:A2)*2,1)")

    # ===== 3. 数值 / 字符串 / #REF! 还原 =====
    check("整值规范化 1.0→1", r("=1.0"), "1")
    check("负数整值", r("=-8"), "-8")
    check("字符串引号", r('="a"'), '"a"')
    check("#REF! 节点", r("=#REF!"), "#REF!")

    # ===== 4. 幂等性：render(parse(render(parse(x)))) == render(parse(x)) =====
    fixtures = [
        "=A1+B1", "=SUM(A1:A10)", "=Sheet2!$A$1:B$2",
        '=DATA("周","业务收入",2025,1)*PARAM("提成比例")',
        "=-(A1+B1)", "=IF(A1>=100,1,0)", "=2^3^2",
        "=ROUND(SUM(A1:A2)*2,1)", "=#REF!+A1", "=A1-(B1+C1)",
    ]
    for f in fixtures:
        first = r(f)
        again = r("=" + first)
        check(f"幂等 {f}", again, first)

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
