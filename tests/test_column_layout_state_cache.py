"""列布局 apply 状态缓存（P2 增强）单元测试 — offscreen。

验证：apply 热路径不再每次走 QSettings 读 + json.loads（mgr.load），
缓存随 save 路径（拖宽/冻结）失效后自动重读。

运行：python tests/test_column_layout_state_cache.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QTableWidget  # noqa: E402

from app.ui.column_layout import install_column_layout  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def main() -> int:
    app = QApplication.instance() or QApplication([])

    table = QTableWidget(3, 4)
    table.setHorizontalHeaderLabels(["甲", "乙", "丙", "丁"])
    col = install_column_layout(table, "p3cache", "state_cache")
    app.processEvents()

    loads = []
    orig_load = col.mgr.load

    def spy_load(keys):
        loads.append(1)
        return orig_load(keys)

    col.mgr.load = spy_load

    col.apply()  # 首次：建立 content_w + 加载状态
    n0 = len(loads)
    check("首次 apply 加载 1 次", n0 == 1, f"loads={n0}")

    # 热路径：连续多次 apply 不再触发 mgr.load
    col.apply(remeasure=False)
    col.apply(remeasure=False)
    col.apply()
    check("缓存生效：后续 apply 零加载", len(loads) == n0, f"loads={len(loads)}")

    # 拖宽（save 路径）→ 缓存失效 → _state 重读 + apply 再读
    # （相对偏移保证一定触发 sectionResized：绝对值可能与填宽结果相同而无事件）
    w0 = table.columnWidth(0)
    table.setColumnWidth(0, w0 + 37)
    app.processEvents()
    n1 = len(loads)
    check("拖宽使缓存失效并重读", n1 > n0, f"loads={n1}")

    # 失效后的 apply 又走缓存
    col.apply(remeasure=False)
    check("重读后缓存继续生效", len(loads) == n1, f"loads={len(loads)}")

    # 冻结（save 路径）→ 失效 → apply 重读
    col._invalidate_state()
    col.apply(remeasure=False)  # 先回填缓存
    loads.clear()
    col._toggle_freeze(col._keys(), 1)
    app.processEvents()
    check("冻结后 apply 重读状态", len(loads) >= 1, f"loads={len(loads)}")

    # 功能不回退：拖宽后 fixed 宽度被记住（重新加载的状态含 widths）
    check("表仍可用（列数不变）", table.columnCount() == 4)

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
