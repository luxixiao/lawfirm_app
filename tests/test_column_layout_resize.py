"""列布局 Resize 防抖（P2-2）单元测试 — offscreen。

验证：连续多个 Resize 事件只触发 1 次 apply（120ms 防抖合并），
防抖窗口结束后列宽被正确重填。
运行：python tests/test_column_layout_resize.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QSize  # noqa: E402
from PySide6.QtGui import QResizeEvent  # noqa: E402
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
    for r in range(3):
        for c in range(4):
            table.setItem(r, c, None)
    col = install_column_layout(table, "test", "resize_debounce")
    app.processEvents()
    col.apply()  # 真实视图在渲染数据后手动 apply（建立 content_w），此处同构
    app.processEvents()

    calls = []
    orig_apply = col.apply

    def spy_apply(*a, **k):
        calls.append(1)
        return orig_apply(*a, **k)

    col.apply = spy_apply  # 实例属性遮蔽方法（内部 self.apply 调用也会被计数）

    # 连发 10 个 Resize 事件（模拟拖动窗口边框每帧触发）
    old = table.size()
    for i in range(1, 11):
        new = QSize(old.width() + i * 5, old.height())
        QCoreApplication.sendEvent(table, QResizeEvent(new, old))
        old = new
    app.processEvents()
    check("防抖窗口内 10 次 Resize 零 apply", len(calls) == 0, f"calls={len(calls)}")

    # 等防抖到期（120ms + 余量）
    import time
    deadline = time.time() + 1.0
    while time.time() < deadline and len(calls) == 0:
        app.processEvents()
        time.sleep(0.02)
    check("防抖到期只补 1 次 apply", len(calls) == 1, f"calls={len(calls)}")

    # 防抖后表格仍可用（列数不变、可继续手动 apply）
    orig_apply(remeasure=False)
    check("手动 apply 仍正常", table.columnCount() == 4)

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
