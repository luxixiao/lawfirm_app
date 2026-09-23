"""连接卫生 AST 门禁（P1-2）——静态扫描 app/，防止连接/求值器泄漏回归。

规则 A（必漏型）：get_conn() 的返回值未绑定变量、直接作为另一个调用的实参
    → 调用方拿不到引用，该连接无人负责关闭（如下钻弹窗一次性连接）。
规则 B（条件漏型）：函数内 `x = get_conn()` / `x = CalcEvaluator(...)` /
    `x = CalcData(...)` 持有连接后，函数内不存在任何落在 finally 段里的
    close() 调用 → 任一异常路径都会泄漏（如导出求值中途抛错）。

命中即失败。若出现新的合法形态，先修代码再考虑扩展规则，禁止随手加白名单。
运行：python tests/test_conn_hygiene.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
OWNERS = {"get_conn", "CalcEvaluator", "CalcData"}

OK, FAILS = 0, []


def _contains_owner_call(node) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in OWNERS
        for n in ast.walk(node)
    )


def scan_file(path: Path):
    """返回 [(func_name, line, rule, desc), ...]"""
    hits = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # ---- 规则 A：get_conn() 直接作为调用实参 ----
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            args = list(node.args) + [kw.value for kw in node.keywords]
            for arg in args:
                if (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
                        and arg.func.id == "get_conn"):
                    hits.append((fn.name, arg.lineno, "A",
                                 "get_conn() 内联作实参，调用方拿不到引用无人可关"))
        # ---- 规则 B：持有连接的赋值，但函数内没有 finally 段保护的 close ----
        owned = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and _contains_owner_call(node.value):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        owned.append((t.id, node.lineno))
        if not owned:
            continue
        finally_closes = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Try):
                for h in node.finalbody:
                    for x in ast.walk(h):
                        if (isinstance(x, ast.Call)
                                and isinstance(x.func, ast.Attribute)
                                and x.func.attr == "close"):
                            finally_closes.add(x.lineno)
        if not finally_closes:
            for name, ln in owned:
                hits.append((fn.name, ln, "B",
                             f"{name} 持有连接/求值器，但函数内无任何 finally 保护"))
    return hits


def main() -> int:
    for py in sorted(APP.rglob("*.py")):
        rel = py.relative_to(ROOT).as_posix()
        for fname, ln, rule, desc in scan_file(py):
            FAILS.append(f"{rel}:{ln} [规则{rule}] {fname}: {desc}")
    if FAILS:
        print("FAILED:\n" + "\n".join(FAILS))
    else:
        print(f"PASS 连接卫生检查（规则 A/B，扫描 app/ 全部 {len(list(APP.rglob('*.py')))} 个文件）")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
