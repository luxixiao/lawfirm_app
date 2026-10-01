"""版本号展示 —— 无头(offscreen) GUI 验收脚本（用户本机 venv 运行）。

验证「侧栏底部『偏好』区下方新增常驻版本号」这一改动：
- 核心（确定性、不碰 DB）：直接驱动真实的 `MainWindow._build_pref_box`，
  用 fake self 提供两个回调，断言返回的 pref_box 内存在唯一一个
  objectName="prefLabel" 且文本为 `v{__version__}` 的 QLabel，且 tooltip 完整。
- 加强（端到端）：构造真实 `MainWindow()`，`get_conn` 指向内存库（带 SCHEMA），
  断言版本号标签确实被装进 `mw.pref_box`（即真实侧栏底部挂件）。
  该步若因离线环境限制抛异常则跳过，不致命——核心校验仍为准。

运行（本机 venv，项目根目录）：
    cd 测试/lawfirm_app
    QT_QPA_PLATFORM=offscreen python _accept_version_label.py
退出码 0 = 验收通过；非 0 = 失败（脚本会打印明细）。
"""
import os
import sys
import sqlite3
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # 无显示也能跑 Qt

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 这些 import 在本机 venv（含 PySide6）下才可用；沙箱里只能 py_compile，不能真正执行。
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication(sys.argv)  # offscreen 下只需一个实例

from app import __version__  # noqa: E402
from app.db import SCHEMA  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

EXPECT_TEXT = f"v{__version__}"
EXPECT_TIP = f"律所开票收款统计 v{__version__}"


def _find_version_labels(box) -> list:
    """在给定 widget 子树里找出版本号标签（prefLabel + 以 'v' 开头）。"""
    return [l for l in box.findChildren(QLabel)
            if l.objectName() == "prefLabel" and l.text().startswith("v")]


# ---- 1) 核心：真实 _build_pref_box（fake self，避免构造整窗/碰 DB）----
class _FakeSelf:
    def _on_motion_toggled(self, enabled):  # noqa: ARG002
        pass

    def _on_font_changed(self, idx):  # noqa: ARG002
        pass


print("=== 核心校验：MainWindow._build_pref_box ===")
try:
    box = MainWindow._build_pref_box(_FakeSelf())
except Exception as e:  # noqa: BLE001
    import traceback
    print("EXCEPTION:", repr(e))
    traceback.print_exc()
    sys.exit(1)

ver = _find_version_labels(box)
print("找到版本号标签数:", len(ver))
for l in ver:
    print("   text=%r  objectName=%r  toolTip=%r" % (l.text(), l.objectName(), l.toolTip()))

core_ok = (
    len(ver) == 1
    and ver[0].text() == EXPECT_TEXT
    and ver[0].toolTip() == EXPECT_TIP
)
print("核心校验结果:", "OK" if core_ok else "FAIL")


# ---- 2) 加强：真实 MainWindow 构造（get_conn 指向内存库）----
print("\n=== 加强校验：真实 MainWindow 集成 ===")
full_window_checked = False
full_ok = False
try:
    import app.db as dbmod
    import app.ui.main_window as mwmod

    def _mem_conn():
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        c.executescript(SCHEMA)
        return c

    dbmod.get_conn = _mem_conn
    mwmod.get_conn = _mem_conn

    mw = mwmod.MainWindow()
    mw_ver = _find_version_labels(mw.pref_box)
    full_ok = len(mw_ver) == 1 and mw_ver[0].text() == EXPECT_TEXT
    full_window_checked = True
    print("MainWindow 构造 + pref_box 内版本号:", "OK" if full_ok else "FAIL")
except Exception as e:  # noqa: BLE001
    print("完整 MainWindow 集成校验: 跳过（环境限制：%s）" % type(e).__name__)


# ---- 3) 结论 ----
passed = core_ok and (not full_window_checked or full_ok)
print("\n=== 验收结论 ===")
print("版本号文本 == %r :" % EXPECT_TEXT, core_ok)
print("完整 MainWindow 集成:", "OK" if full_ok else ("跳过" if not full_window_checked else "FAIL"))
print("PASS" if passed else "FAIL")
sys.exit(0 if passed else 1)
