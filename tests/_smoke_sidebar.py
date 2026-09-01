"""sidebar 无头冒烟 — 自绘图标 + hover/press/chevron + 折叠/展开动效（offscreen）。

运行：QT_QPA_PLATFORM=offscreen python tests/_smoke_sidebar.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="sidebar_smoke_"))
import app.ui.style as style  # noqa: E402
style.PREFS_PATH = TMP / "prefs.json"      # 动效开关写临时文件，不污染真实 prefs

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QPushButton, QVBoxLayout, QWidget,
)
from PySide6.QtTest import QTest  # noqa: E402

app = QApplication.instance() or QApplication([])
style.apply_skin(app, "notion_light")

from app.ui import sidebar as sb  # noqa: E402
from app.ui.nav_icons import NAV_ICON_PATHS, draw_nav_icon  # noqa: E402

GROUPS = [
    ("数据导入", [("import", "导入台账"), ("batch", "导入记录")]),
    ("台账查看", [("ledger", "销项发票")]),
    ("业务数据", [("invoice", "发票收款情况"), ("refund", "退款")]),
    ("工资个税", [("salary", "工资累计")]),
    ("分成计算", [("calc", "计算表")]),
    ("各类报表", [("settlement", "各类报表")]),
    ("数据维护", [("staff", "员工管理"), ("expense_cat", "费用类型")]),
]

fails, OK = [], 0


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        fails.append(f"{label} {detail}".strip())


def render_icon(name: str, size: int = 24) -> bytes:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    draw_nav_icon(p, name, QRectF(0, 0, size, size), QColor("#37352F"))
    p.end()
    return bytes(img.constBits())


def ink_pixels(name: str, size: int = 24) -> int:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    draw_nav_icon(p, name, QRectF(0, 0, size, size), QColor("#37352F"))
    p.end()
    return sum(1 for x in range(size) for y in range(size)
               if img.pixelColor(x, y).alpha() > 40)


# ===== 1. 图标：七枚齐全、各自有内容、两两不同 =====
names = [g[0] for g in GROUPS]
check("7 个大类图标都有定义", all(n in NAV_ICON_PATHS for n in names),
      f"缺={[n for n in names if n not in NAV_ICON_PATHS]}")
for n in names:
    check(f"{n} 图标非空", ink_pixels(n) > 60, f"ink={ink_pixels(n)}")

sig = {n: render_icon(n) for n in names}
dupes = [(a, b) for i, a in enumerate(names) for b in names[i + 1:] if sig[a] == sig[b]]
check("七枚图标两两不同", not dupes, f"重复对={dupes}")

# 未知组名回退到齿轮，不应变成空白
check("未知图标回退有内容", ink_pixels("不存在的组") > 60)
check("未知图标回退=数据维护", render_icon("不存在的组") == sig["数据维护"])

# ===== 2. 侧栏结构 =====
bottom = QWidget()
lay = QVBoxLayout(bottom)
lay.addWidget(QLabel("皮肤"))
w = sb.SidebarWidget(GROUPS, bottom_widget=bottom)
w._pinned = False      # QSettings 读的是真实注册表，这里强制"未固定"以验证自动展开
w.resize(240, 700)
w.show()
app.processEvents()

check("7 个大类按钮", len(w._header_buttons) == 7, f"got={len(w._header_buttons)}")
check("self 名称=sidebar", w.objectName() == "sidebar")
for n in names:
    btn = w._header_buttons[n]
    check(f"{n} 有 tooltip（收起态可读）", btn.toolTip() == n, f"got={btn.toolTip()}")
    check(f"{n} 行高 34", btn.height() == sb._px(sb.BASE_ROW_GROUP), f"got={btn.height()}")
check("子项共 10 个", len(w._item_buttons) == 10, f"got={len(w._item_buttons)}")
check("子项可勾选", all(b.isCheckable() for b in w._item_buttons.values()))

# effect 只能有一层：嵌套 effect 会让 Qt 对同一 widget 再开 painter，
# 刷 "A paint device can only be painted by one painter at a time"
holders = [wd for wd in [w] + w.findChildren(QWidget) if wd.graphicsEffect() is not None]
check("effect 仅 7 个 GroupPanel（无嵌套）", len(holders) == 7, f"got={len(holders)}")
check("effect 不挂在大类按钮上",
      all(w._header_buttons[n].graphicsEffect() is None for n in names))

# ===== 3. 收起 / 展开（无动画路径） =====
w.set_collapsed(True, animate=False)
app.processEvents()
check("收起宽度 60", w.width() == sb._px(sb.BASE_W_COLLAPSE), f"got={w.width()}")
check("收起态不画文字", all(not b._show_text for b in w._header_buttons.values()))
check("收起态隐藏底部挂件", bottom.isHidden())
check("收起态隐藏固定按钮", w.pin_btn.isHidden())

w.set_collapsed(False, animate=False)
app.processEvents()
check("展开宽度 240", w.width() == sb._px(sb.BASE_W_EXPAND), f"got={w.width()}")
check("展开态画文字", all(b._show_text for b in w._header_buttons.values()))
check("展开态显示底部挂件", bottom.isVisible())
check("展开态显示固定按钮", w.pin_btn.isVisible())

# ===== 4. 动效：hover / press / chevron =====
btn = w._header_buttons["数据导入"]
btn._run("hoverProgress", 1.0, "hover")
QTest.qWait(200)
check("hover 动画到位", abs(btn._hover - 1.0) < 0.01, f"got={btn._hover}")

btn._run("pressProgress", 1.0, "press")
QTest.qWait(140)
check("press 动画到位", abs(btn._press - 1.0) < 0.01, f"got={btn._press}")
btn._run("pressProgress", 0.0, "press")
QTest.qWait(140)
check("press 归零", abs(btn._press) < 0.01, f"got={btn._press}")

btn.set_expanded(True)
QTest.qWait(260)
check("chevron 展开=90°", abs(btn._angle - 90.0) < 0.5, f"got={btn._angle}")
btn.set_expanded(False)
QTest.qWait(260)
check("chevron 折叠=0°", abs(btn._angle) < 0.5, f"got={btn._angle}")

# ===== 5. 组折叠 / 展开动画 =====
panel = w._panels["业务数据"]
panel.set_open(False, animate=True)
QTest.qWait(300)
check("折叠后不可见", panel.isHidden())
check("折叠后高度归零", panel.maximumHeight() == 0, f"got={panel.maximumHeight()}")
check("折叠后透明度 0", abs(panel._eff.opacity()) < 0.01, f"got={panel._eff.opacity()}")

panel.set_open(True, animate=True)
app.processEvents()
w.grab()                 # 动画进行中抓取：若 effect 嵌套，这里会触发双 painter 告警
QTest.qWait(400)
check("展开后可见", panel.isVisible())
check("展开后高度复位", panel.maximumHeight() == 16777215, f"got={panel.maximumHeight()}")
check("展开后高度>0", panel.height() > 0, f"h={panel.height()}")
check("展开后透明度 1", abs(panel._eff.opacity() - 1.0) < 0.01)
check("子项可见", all(btn.isVisible() for btn in panel.findChildren(QPushButton)))

# 连点不叠加：连续两次切换后状态自洽
panel.set_open(False, animate=False)
panel.set_open(False, animate=False)
check("连点折叠状态自洽", panel.isHidden() and panel.is_open() is False)
panel.set_open(True, animate=False)
check("连点展开状态自洽", panel.isVisible() and panel.is_open() is True)

# ===== 6. 侧栏宽度动画 =====
w.set_collapsed(True, animate=True)
QTest.qWait(400)
check("宽度动画收起到 60", w.width() == sb._px(sb.BASE_W_COLLAPSE), f"got={w.width()}")
w.set_collapsed(False, animate=True)
QTest.qWait(400)
check("宽度动画展开到 240", w.width() == sb._px(sb.BASE_W_EXPAND), f"got={w.width()}")
check("动画结束后已固定宽度", w.minimumWidth() == sb._px(sb.BASE_W_EXPAND), f"got={w.minimumWidth()}")
check("动画后 min=max（单一状态量，不残留 min/max 分叉）",
      w.minimumWidth() == w.maximumWidth() == sb._px(sb.BASE_W_EXPAND),
      f"min={w.minimumWidth()} max={w.maximumWidth()}")

# ===== 7. 防抖：鼠标掠过不应立即展开/收起 =====
w.set_collapsed(True, animate=False)
w._enter_timer.stop()
w.enterEvent(None)
check("移入后未立即展开（防抖）", w.width() == sb._px(sb.BASE_W_COLLAPSE), f"got={w.width()}")
QTest.qWait(80 + 240 + 120)          # 防抖 80 + 宽度动画 240 + 余量
check("防抖结束后已展开", w.width() == sb._px(sb.BASE_W_EXPAND), f"got={w.width()}")
w.leaveEvent(None)
check("移出后未立即收起（防抖）", w.width() == sb._px(sb.BASE_W_EXPAND), f"got={w.width()}")
QTest.qWait(120 + 180 + 120)         # 防抖 120 + 宽度动画 180 + 余量
check("防抖结束后已收起", w.width() == sb._px(sb.BASE_W_COLLAPSE), f"got={w.width()}")

# ===== 8. 关闭动效：瞬时到位 =====
style.set_motion_enabled(False)
check("动效开关已关闭", style.motion_enabled() is False)
w.apply_motion_pref()
app.processEvents()
panel.set_open(True, animate=True)
app.processEvents()
check("关动效后展开瞬时到位", panel.isVisible() and panel.maximumHeight() == 16777215)
w.set_collapsed(False, animate=True)
app.processEvents()
check("关动效后宽度瞬时到位", w.width() == sb._px(sb.BASE_W_EXPAND), f"got={w.width()}")
style.set_motion_enabled(True)

# ===== 9. 深色皮肤下自绘仍可渲染 =====
style.apply_skin(app, "notion_dark")
app.processEvents()
dark_ink = ink_pixels("各类报表")
check("深色皮肤图标仍绘制", dark_ink > 60, f"ink={dark_ink}")
style.apply_skin(app, "notion_light")

# ===== 10. 布局回归：宽表格页面不应把主窗口最小宽度撑爆 =====
# 现象：QStackedWidget 的 minimumSizeHint 取所有页面的最大值，宽表格页能顶到 1700+，
# 超过 1600 宽的屏幕后 Windows 拒绝 setGeometry 并反复重试，刷 QWindowsWindow 告警。
from PySide6.QtWidgets import (  # noqa: E402
    QHBoxLayout, QMainWindow, QStackedWidget, QTableWidget, QTableWidgetItem,
)

win = QMainWindow()
stack = QStackedWidget()
page = QWidget()                      # 模拟多表并排的宽页面（结算页那类）
row = QHBoxLayout(page)
for _ in range(3):
    t = QTableWidget(3, 12)
    for c in range(12):
        t.setHorizontalHeaderItem(c, QTableWidgetItem(f"列标题-{c}-加长"))
    row.addWidget(t)
page.setMinimumWidth(1700)            # 复现：页面自身最小宽度大于屏幕
stack.addWidget(page)
win.setCentralWidget(stack)
app.processEvents()
before = win.minimumSizeHint().width()
check("宽页面确实会顶大主窗口最小宽度（复现告警成因）", before > 1600, f"before={before}")
# main_window 的实际修复：显式给主窗口设最小尺寸，覆盖布局算出来的巨大值
win.setMinimumSize(1100, 650)
app.processEvents()
check("显式最小宽度生效，不再要求 1700+", win.minimumWidth() == 1100,
      f"got={win.minimumWidth()}")

# ===== 11. 像素冒烟 =====
w.set_collapsed(False, animate=False)
app.processEvents()
img = w.grab()
qi = img.toImage()
nonwhite = sum(1 for x in range(0, qi.width(), 8)
               for y in range(0, qi.height(), 8)
               if qi.pixelColor(x, y).red() < 240)
check("侧栏像素非全白", nonwhite > 20, f"nonwhite={nonwhite}")
img.save(str(ROOT / "tests" / "_smoke_sidebar.png"))

print(f"SMOKE PASS {OK}" if not fails else "SMOKE FAILED:\n" + "\n".join(fails))
sys.exit(0 if fails == [] else 1)
