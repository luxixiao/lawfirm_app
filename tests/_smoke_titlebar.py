"""offscreen 冒烟：验证 AppTitleBar（B 方案 + P1/P2/P3）。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor

app = QApplication.instance() or QApplication(sys.argv)

import app.ui.main_window as m
from app.ui import style
from app.ui import scale as _scale

# 隔离用户偏好：scale 读 prefs.json 的 font_step（用户可能停在非默认档），
# 而下方宽度断言按默认档硬编码。set_step 仅改内存，不写 prefs.json。
_scale.set_step(_scale.DEFAULT_STEP)

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")


# 1) 构造标题栏
tb = m.AppTitleBar()
tb.resize(1280, 36)  # 模拟窗口宽度
check("AppTitleBar 构造", tb is not None)

# 2) B 方案：动态页面名（首屏 import -> 导入台账）
tb.set_page_title("导入台账")
check("set_page_title 生效", tb.titleLabel.text() == "导入台账",
      f"got={tb.titleLabel.text()!r}")

# 3) P3：长标题截断（宽度压到 200px，超长标题必触发省略号，且不压右侧按钮）
tb.resize(200, 36)
long_name = "经办人发票收款情况" * 3  # 超长，确保触发截断
tb.set_page_title(long_name)
elided = tb.titleLabel.text()
check("长标题被省略号截断", elided.endswith("…") and elided != long_name,
      f"got={elided!r}")
# 残留宽度不超可用（右侧按钮 138 + 左 padding 12 + 余量）
fm = tb.titleLabel.fontMetrics()
check("截断后宽度不压按钮", fm.horizontalAdvance(elided) <= 200 - 150,
      f"w={fm.horizontalAdvance(elided)}")

# 4) 空标题
tb.set_page_title("")
check("空标题显示空", tb.titleLabel.text() == "")

# 5) P1：按钮配色被设置（非默认纯黑图标），关闭按钮 hover 红底
light = style.PALETTES["notion_light"]
dark = style.PALETTES["notion_dark"]

# 浅色：图标色应等于浅色 text，关闭 hover 红底
style._current_skin = "notion_light"
tb.apply_skin()
check("浅色图标色=text", tb.minBtn.getNormalColor().name() == QColor(light["text"]).name(),
      f"got={tb.minBtn.getNormalColor().name()}")
check("浅色关闭hover红底", tb.closeBtn.getHoverBackgroundColor().name() == "#e81123",
      f"got={tb.closeBtn.getHoverBackgroundColor().name()}")
check("浅色关闭hover白图标", tb.closeBtn.getHoverColor() == QColor(255, 255, 255))

# 深色：图标色应等于深色 text（不再是纯黑不可见）
style._current_skin = "notion_dark"
tb.apply_skin()
check("深色图标色=text(可见)", tb.minBtn.getNormalColor().name() == QColor(dark["text"]).name(),
      f"got={tb.minBtn.getNormalColor().name()}")
style._current_skin = "notion_light"

# 6) P2：分隔线样式存在
ss = tb.styleSheet()
check("底部分隔线 border-bottom", "border-bottom" in ss, f"ss={ss[:60]!r}")

# 7) grab 不死
pix = tb.grab()
check("grab 成功", pix.width() > 0 and pix.height() > 0)

failed = [n for n, ok, _ in results if not ok]
print("\n==== SUMMARY ====", "ALL PASS" if not failed else f"FAILED: {failed}")
sys.exit(1 if failed else 0)
