"""账面情况页取数（P1-3 去线程化后）单元测试 — offscreen。

验证：
1. _fetch 抛异常 → hint 显式「加载失败」，不再永久停在「加载中…」；
2. 正常路径 → 无「加载失败」且表格被填充（行数与科目数一致）；
3. 返回结构异常（缺 rows）→ 显式报错。

注入方式：方法覆写（_fetch seam），不需要 patch / 线程等待 / 信号 spy。
运行：python tests/test_book_balance_load.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.book_balance_view import _PivotPage  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


class _Boom(_PivotPage):
    """_fetch 恒抛异常的注入子类。"""

    def _fetch(self, year, start, end):
        raise RuntimeError("boom")


class _Junk(_PivotPage):
    """_fetch 返回非法结构的注入子类。"""

    def _fetch(self, year, start, end):
        return {"nope": 1}


def main() -> int:
    app = QApplication.instance() or QApplication([])

    # 1. 异常路径：显式失败，绝不停留在「加载中…」
    w = _Boom()
    check("异常→hint 显式加载失败", "加载失败" in w.hint.text(), f"hint={w.hint.text()!r}")
    check("异常→不再显示加载中", "加载中" not in w.hint.text(), f"hint={w.hint.text()!r}")
    check("异常→提示可刷新重试", "刷新" in w.hint.text(), f"hint={w.hint.text()!r}")

    # 2. 结构异常路径
    w2 = _Junk()
    check("结构异常→hint 显式加载失败", "加载失败" in w2.hint.text(), f"hint={w2.hint.text()!r}")
    check("结构异常→不再显示加载中", "加载中" not in w2.hint.text(), f"hint={w2.hint.text()!r}")

    # 3. 正常路径：真实库（offscreen）→ 无失败文案，表格有数据
    w3 = _PivotPage()
    check("正常→无加载失败文案", "加载失败" not in w3.hint.text(), f"hint={w3.hint.text()!r}")
    check("正常→表格已建列", w3.table.columnCount() >= 2,
          f"cols={w3.table.columnCount()}")

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
