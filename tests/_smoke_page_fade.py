"""页面切换淡入动画冒烟：animate_page_in

断言：
  1. 动效开：调用后页面挂 QGraphicsOpacityEffect，初始 opacity < 1
  2. 动画结束（约 200ms）后 effect 被移除（不常驻滤镜）
  3. 再切另一页时旧 effect 先清理（单飞，同一时刻只有一层 effect）
  4. 动效关（prefs motion=false）：调用为 no-op，不挂 effect

运行：
  QT_QPA_PLATFORM=offscreen python tests/_smoke_page_fade.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="fade_"))
import app.ui.style as style  # noqa: E402
style.PREFS_PATH = TMP / "prefs.json"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QElapsedTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget  # noqa: E402
from PySide6.QtWidgets import QGraphicsOpacityEffect  # noqa: E402
from app.ui.widgets import animate_page_in  # noqa: E402

app = QApplication.instance() or QApplication([])
style.apply_skin(app, "notion_light")

stack = QStackedWidget()
p1, p2 = QWidget(), QWidget()
stack.addWidget(p1)
stack.addWidget(p2)
stack.resize(800, 600)
stack.show()
app.processEvents()

ok = True

# -- 动效开：淡入并自动清理 --
style.set_motion_enabled(True)
animate_page_in(p2)
eff = p2.graphicsEffect()
ok1 = isinstance(eff, QGraphicsOpacityEffect) and eff.opacity() < 1.0
print(f"step1 挂载淡入效果: {type(eff).__name__ if eff else None} opacity={eff.opacity() if eff else '-'} -> {'OK' if ok1 else 'FAIL'}")
ok &= ok1

timer = QElapsedTimer()
timer.start()
while timer.elapsed() < 600:
    app.processEvents()
ok2 = p2.graphicsEffect() is None
print(f"step2 动画结束移除效果: effect={p2.graphicsEffect()} -> {'OK' if ok2 else 'FAIL'}")
ok &= ok2

# -- 单飞：切第三页时旧页 effect 已清、新页只有一层 --
animate_page_in(p1)
app.processEvents()
n_eff = sum(1 for w in (p1, p2) if w.graphicsEffect() is not None)
ok3 = n_eff == 1 and p1.graphicsEffect() is not None
print(f"step3 单飞清理: 带效果页数={n_eff} -> {'OK' if ok3 else 'FAIL'}")
ok &= ok3
timer.restart()
while timer.elapsed() < 600:
    app.processEvents()

# -- 动效关：no-op --
style.set_motion_enabled(False)
animate_page_in(p2)
ok4 = p2.graphicsEffect() is None
print(f"step4 关动效为 no-op: effect={p2.graphicsEffect()} -> {'OK' if ok4 else 'FAIL'}")
ok &= ok4
style.set_motion_enabled(True)

print("PAGE_FADE:", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
