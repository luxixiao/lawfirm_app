"""页头帮助组件无头冒烟：HelpIcon 自绘 + 悬浮卡显隐 + 边界夹紧 + 动效开关。

运行：QT_QPA_PLATFORM=offscreen python tests/_smoke_page_header.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="helptip_smoke_"))
import app.ui.style as style  # noqa: E402
style.PREFS_PATH = TMP / "prefs.json"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QEnterEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

app = QApplication.instance() or QApplication([])
style.apply_skin(app, "notion_light")
from app.ui import scale  # noqa: E402
from app.ui.widgets import page_header, HelpIcon, HelpTip  # noqa: E402

fails, OK = [], 0


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        fails.append(f"{label} {detail}".strip())


def enter(w):
    w.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))


def leave(w):
    w.leaveEvent(QEvent(QEvent.Type.Leave))


# ===== 1. 构造：有/无说明（先 show 容器，可见性才成立）=====
hdr = page_header("销项发票", "这是说明文本，用于验证悬浮卡渲染、自动折行与边界夹紧。")
hdr_no = page_header("无说明页")
icon = hdr.findChild(HelpIcon)
icon_no = hdr_no.findChild(HelpIcon)
holder = QWidget()
hl = QVBoxLayout(holder)
hl.addWidget(hdr)
hl.addWidget(hdr_no)
holder.resize(900, 240)
holder.show()
app.processEvents()
check("PageHeader 构造", isinstance(hdr, QWidget))
check("有说明→图标可见", icon is not None and icon.isVisible(), f"vis={icon.isVisible() if icon else 'none'}")
check("无说明→图标隐藏", icon_no is not None and not icon_no.isVisible(), f"vis={icon_no.isVisible() if icon_no else 'none'}")

# ===== 2. 自绘不崩：离屏渲染图标 =====
try:
    px = icon.grab()
    check("图标可离屏渲染", not px.isNull() and px.width() == scale.px(24), f"w={px.width()}")
except Exception as e:  # noqa: BLE001
    check("图标可离屏渲染", False, str(e))

# 无说明的图标不应自绘（paintEvent 直接返回，grab 为透明但不崩）
try:
    icon_no.grab()
    check("无说明图标 grab 不崩", True)
except Exception as e:  # noqa: BLE001
    check("无说明图标 grab 不崩", False, str(e))

# ===== 3. 悬浮卡：显隐 + 尺寸 + 内容 =====
enter(icon)
QTest.qWait(180 + 260)  # 180ms 防误触延迟 + help_in 动画
tip = icon._tip
check("悬停后卡片出现", tip is not None and tip.isVisible(), f"tip={tip}")
if tip is not None:
    check("卡片宽度 ≤ 420", tip.width() <= scale.px(420), f"w={tip.width()}")
    check("卡片高度 > 0", tip.height() > 0, f"h={tip.height()}")
    check("卡片含说明文本", "说明" in (tip._text or ""))
    # 边界夹紧：卡片整体应落在主屏可用区域内
    sr = app.primaryScreen().availableGeometry()
    tr = tip.geometry()
    check("卡片在屏内(右/下不越界)", tr.right() <= sr.right() and tr.bottom() <= sr.bottom(),
          f"tip={tr} screen={sr}")

# 离开 → grace 80ms + 退场 120ms 后关闭
leave(icon)
QTest.qWait(80 + 200)
check("离开后卡片关闭", tip is not None and not tip.isVisible(), f"vis={tip.isVisible() if tip else 'n/a'}")

# ===== 4. 长文本折行：宽度仍受 420 限制 =====
long_tip = HelpTip("律所管理系统用于管理发票、收款、费用、工资、个税、预收款、分成计算等台账。"
                   "本页展示销项发票（正数发票）的明细，可按对方、经办人、金额、日期筛选，"
                   "并支持导出为单文件多 sheet 的 Excel。所有金额单位为元，红字表示红冲。")
long_tip.adjustSize()
check("长文本卡片宽度 ≤ 420", long_tip.width() <= scale.px(420), f"w={long_tip.width()}")
check("长文本卡片高度 > 单行", long_tip.height() > scale.px(20), f"h={long_tip.height()}")
long_tip.deleteLater()

# ===== 5. reduced-motion：瞬时落位 =====
style.set_motion_enabled(False)
enter(icon)
QTest.qWait(180 + 160)  # 180ms 延迟 + 1ms 瞬时动画余量
check("关动效后卡片仍可显示", tip is not None and tip.isVisible(), f"vis={tip.isVisible() if tip else 'n/a'}")
leave(icon)
QTest.qWait(80 + 200)   # 80ms grace + 1ms 退场余量
check("关动效后卡片可关闭", not tip.isVisible())
style.set_motion_enabled(True)

# ===== 6. 深色皮肤下自绘仍可渲染 =====
style.apply_skin(app, "notion_dark")
app.processEvents()
px_dark = icon.grab()
check("深色皮肤图标可渲染", not px_dark.isNull() and px_dark.width() > 0)
style.apply_skin(app, "notion_light")

# ===== 7. 像素冒烟：整体页头截图 =====
holder.resize(900, 240)
holder.show()
app.processEvents()
try:
    img = holder.grab()
    qi = img.toImage()
    nonwhite = sum(1 for x in range(0, qi.width(), 8) for y in range(0, qi.height(), 8)
                   if qi.pixelColor(x, y).red() < 240)
    check("页头像素非全白", nonwhite > 10, f"nonwhite={nonwhite}")
    img.save(str(ROOT / "tests" / "_smoke_page_header.png"))
except Exception as e:  # noqa: BLE001
    check("页头像素非全白", False, str(e))

print(f"SMOKE PASS {OK}" if not fails else "SMOKE FAILED:\n" + "\n".join(fails))
sys.exit(0 if fails == [] else 1)
