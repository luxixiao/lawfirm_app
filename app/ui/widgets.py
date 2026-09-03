"""Notion 风格控件垫片

把 qfluentwidgets 的"样式型"控件替换为纯 Qt 控件 + style.py 的 QSS 控制外观，
彻底去掉 Fluent 混搭。视图只需把 `from qfluentwidgets import (...)` 改成
`from app.ui.widgets import (...)`，构造调用保持不变（同名、同参数）。

保留 qfluentwidgets 仅用于 main_window 的 InfoBar toast。
"""
from __future__ import annotations

from html import escape

from PySide6.QtCore import (
    Property,
    QParallelAnimationGroup,
    QRect,
    QRectF,
    Qt,
    QPropertyAnimation,
    QEasingCurve,
    QTimer,
)
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen, QPalette
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QComboBox,
    QLineEdit,
    QStyle,
    QStyleOptionViewItem,
    QTableWidget,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QStackedWidget,
    QTabBar,
    QWidget,
)

from app.ui import scale, style
from app.ui.sidebar import _dur, _curve, _mix


class SubtitleLabel(QLabel):
    """页面主标题 -> #pageTitle（style.py 控制字号/字重/颜色）"""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("pageTitle")


class CaptionLabel(QLabel):
    """辅助说明 / 筛选标签 -> #pageHint（style.py 控制灰色小字）"""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("pageHint")


class PushButton(QPushButton):
    """次级按钮 -> 走全局 QPushButton 样式"""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)


class PrimaryPushButton(QPushButton):
    """主按钮 -> #primary（墨黑填充）"""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("primary")


class ComboBox(QComboBox):
    """下拉框 -> 走全局 QComboBox 样式；兼容 qfluentwidgets 的 addItem(text, userData=)"""

    def addItem(self, text, userData=None):  # type: ignore[override]
        super().addItem(text, userData)


class LineEdit(QLineEdit):
    """单行输入 -> 走全局 QLineEdit 样式"""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)


class TableWidget(QTableWidget):
    """表格 -> 走全局 QTableWidget 样式；兼容 qfluentwidgets 的 setBorderVisible / setBorderRadius（此处为无操作）"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # 自绘表头：支持排序列/冻结列底色高亮（须在设置表头标签之前替换）
        from app.ui.table_features import install_accent_header, install_common_features
        install_accent_header(self)
        # 统一表格行为：像素级滚动 + 选中保色 + 悬停显示被压缩全文
        install_common_features(self)

    def setBorderVisible(self, visible: bool) -> None:  # noqa: D401
        return None

    def setBorderRadius(self, radius: int) -> None:
        return None


class FrozenTableWidget(QTableWidget):
    """首列冻结表格：横向滚动时前 frozen 列固定不动（painter 重绘叠加）

    用法与 QTableWidget 一致，构造后调用 setFrozenColumns(n) 指定冻结列数。
    适用于列多、需要参照首列（序号/姓名/项目）的表格（如结算总表）。
    """

    def __init__(self, *args, frozen: int = 1, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._frozen = max(1, frozen)
        # 冻结区右缘的分隔竖线：默认画（区分冻结区/滚动区）。
        # 部分页面（如结算页）首列本身就紧贴内容，这条线会被误认成「列内右侧的图形」，
        # 可用 setFrozenDividerVisible(False) 关掉。
        self._frozen_divider = True
        # 自绘表头：支持排序列/冻结列底色高亮（须在设置表头标签之前替换）
        from app.ui.table_features import install_accent_header, install_common_features
        install_accent_header(self)
        # 统一表格行为：像素级滚动 + 选中保色 + 悬停显示被压缩全文
        install_common_features(self)
        self.horizontalScrollBar().valueChanged.connect(lambda *_: self.viewport().update())
        self.verticalScrollBar().valueChanged.connect(lambda *_: self.viewport().update())

    def setFrozenColumns(self, n: int) -> None:
        self._frozen = max(1, n)
        self.viewport().update()

    def setFrozenDividerVisible(self, visible: bool) -> None:
        """是否绘制冻结区右缘的分隔竖线（默认 True）。"""
        self._frozen_divider = bool(visible)
        self.viewport().update()

    def frozen_width(self) -> int:
        return sum(self.columnWidth(c) for c in range(self._frozen))

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._frozen <= 0 or self.rowCount() == 0:
            return
        fw = self.frozen_width()
        if fw <= 0:
            return
        # 无横向滚动时，主视图已正确绘制冻结列（与未滚动前完全一致），无需覆盖重绘，
        # 直接画分隔线即可——避免任何无谓的二次绘制。
        if self.horizontalScrollBar().value() == 0:
            if self._frozen_divider:
                painter = QPainter(self.viewport())
                painter.setPen(QColor("#DADAD7"))
                painter.drawLine(fw, 0, fw, self.viewport().height())
            return
        # 发生横向滚动：主视图把冻结列左侧部分（及相邻列左缘）滚到了冻结区之外，
        # 需在冻结区 [0, fw] 内重绘冻结列。关键修复：每个冻结单元格先铺一层不透明底色，
        # 否则单元格背景透明时，相邻列文字会透过冻结列显示，形成「重影」。
        painter = QPainter(self.viewport())
        painter.save()
        painter.setClipRect(0, 0, fw, self.viewport().height())
        first = max(0, self.rowAt(0))
        last = self.rowAt(self.viewport().height() - 1)
        if last < 0:
            last = self.rowCount() - 1
        base_bg = self.palette().color(QPalette.ColorRole.Base)
        for r in range(first, min(last + 1, self.rowCount())):
            y = self.rowViewportPosition(r)
            if y < 0:
                continue
            for c in range(self._frozen):
                idx = self.model().index(r, c)
                opt = QStyleOptionViewItem()
                self.initViewItemOption(opt)
                opt.rect = QRect(0, y, self.columnWidth(c), self.rowHeight(r))
                if self.selectionModel().isSelected(idx):
                    opt.state |= QStyle.StateFlag.State_Selected
                if self.currentIndex() == idx:
                    opt.state |= QStyle.StateFlag.State_HasFocus
                # 先以不透明底色铺满该冻结单元格，擦掉横向滚动泄漏进来的相邻列像素（重影根因）
                painter.fillRect(opt.rect, base_bg)
                self.itemDelegate().paint(painter, opt, idx)
        painter.restore()
        # 冻结列分隔线
        if self._frozen_divider:
            painter.setPen(QColor("#DADAD7"))
            painter.drawLine(fw, 0, fw, self.viewport().height())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # 点击冻结区域 → 映射为选中该行（便于阅读/右键）
        if event.button() == Qt.MouseButton.LeftButton and event.position().x() < self.frozen_width():
            r = self.rowAt(int(event.position().y()))
            if r >= 0:
                self.setCurrentCell(r, 0)
                return
        super().mousePressEvent(event)


# ===========================================================================
# 页头帮助图标 + 悬浮说明卡
#
# 设计（见 design 决策记录）：
# - 图标 A 静默圆点：24×24 透明命中区，正中 14px 正圆、1px 发丝描边、无底色；
#   静止描边 border + 字符 text_mute，hover 描边 border_2 + 字符 text（仅颜色过渡）。
# - 无说明文字时整枚图标隐藏。
# - 悬浮卡 420px 极简：底 bg_table、描边 border_2、圆角 8、正文 12px 行高 1.6、色 text；
#   无箭头、无顶部 accent 线；文字可选中复制；鼠标离图标（且不在卡上）才关。
# - 进场 180ms 延迟防误触；opacity 0→1 + y +6→0（help_in 220ms OutQuart）；
#   退场纯淡出（help_out 120ms OutCubic）；reduced-motion 下均瞬时落位。
# - 动效单一真源在 sidebar.MOTION（help_in / help_out），复用 reduced-motion 开关。
# ===========================================================================
class HelpTip(QLabel):
    """悬浮说明卡本体：纯展示，显隐与定位由 HelpIcon 调度。"""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("pageHelpTip")
        # ToolTip 窗口：无任务栏条目、不抢焦点、置顶；Frameless 确保只显示自绘边框
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setWordWrap(True)
        self.setMaximumWidth(scale.px(420))
        # 用 QWidget 边距承载内边距（QSS padding 不计入 sizeHint，会裁内容）
        self.setContentsMargins(scale.px(12), scale.px(12), scale.px(16), scale.px(16))
        self._eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._eff)
        self._icon = None
        self._text = text
        self._target_x = 0
        self._target_y = 0
        self._slide = 0
        self._show_anim = None
        self._hide_anim = None
        self._set_rich(text)

    def _set_rich(self, text: str) -> None:
        # 先关 wordWrap 量出自然宽度，再按 maxWidth 折行定稿（避免长文本撑出屏幕）
        self.setWordWrap(False)
        self.setText(f'<p style="line-height:160%;margin:0;color:inherit">{escape(text)}</p>')
        self.adjustSize()
        w = min(self.width(), scale.px(420))
        self.setWordWrap(True)
        self.setFixedWidth(w)
        self.adjustSize()

    def _get_slide(self) -> int:
        return self._slide

    def _set_slide(self, v: int) -> None:
        self._slide = int(v)
        self.move(self._target_x, self._target_y + self._slide)

    slide = Property(int, _get_slide, _set_slide)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self._icon is not None:
            self._icon.on_tip_enter()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._icon is not None:
            self._icon.on_tip_leave()
        super().leaveEvent(event)


class HelpIcon(QLabel):
    """页头「?」帮助图标：悬停 180ms 后弹出说明卡；无说明则整枚隐藏。"""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("helpIcon")
        self.setFixedSize(scale.px(24), scale.px(24))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._hover = 0.0
        self._text = text or ""
        self._tip: HelpTip | None = None
        self._anim_hover = None
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.timeout.connect(self._on_show_timeout)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_hide_timeout)
        if not self._text:
            self.hide()

    def set_help_text(self, text: str) -> None:
        """动态更新说明文字；空字符串则隐藏图标。供多 tab 页切 tab 时调用。"""
        self._text = text or ""
        if self._text:
            self.show()
        else:
            self.hide()
        if self._tip is not None and self._tip.isVisible():
            self._tip._set_rich(self._text)
            self._position_tip()

    # -- hover 颜色过渡属性 --
    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, v: float) -> None:
        self._hover = v
        self.update()

    hoverProgress = Property(float, _get_hover, _set_hover)

    def _run_hover(self, end: float) -> None:
        if self._anim_hover is not None:
            self._anim_hover.stop()
        a = QPropertyAnimation(self, b"hoverProgress", self)
        a.setDuration(_dur("hover"))
        a.setEasingCurve(_curve("hover"))
        a.setStartValue(self._hover)
        a.setEndValue(end)
        self._anim_hover = a
        a.start()

    # -- 显隐调度（被 HelpTip 回传） --
    def on_tip_enter(self) -> None:
        self._hide_timer.stop()

    def on_tip_leave(self) -> None:
        self._hide_timer.start(80)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self._text:
            self._run_hover(1.0)
            self._hide_timer.stop()
            self._show_timer.start(180)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._text:
            self._run_hover(0.0)
            self._show_timer.stop()
            if self._tip is not None and self._tip.isVisible():
                gp = self._tip.mapFromGlobal(QCursor.pos())
                if not self._tip.rect().contains(gp):
                    self._hide_timer.start(80)
        super().leaveEvent(event)

    def _on_show_timeout(self) -> None:
        if self._tip is None:
            self._tip = HelpTip(self._text)
            self._tip._icon = self
        self._show_tip()

    def _on_hide_timeout(self) -> None:
        self._hide_tip()

    def _position_tip(self) -> None:
        anchor_bl = self.mapToGlobal(self.rect().bottomLeft())
        tip = self._tip
        x = anchor_bl.x()
        y = anchor_bl.y() + scale.px(6)
        screen = self.screen()
        if screen is not None:
            sr = screen.availableGeometry()
            pad = scale.px(12)
            w = tip.width()
            h = tip.height()
            # 右越界 → 左移（右留 12px）
            if x + w > sr.right() - pad:
                x = sr.right() - pad - w
            if x < sr.left() + pad:
                x = sr.left() + pad
            # 下越界 → 翻到图标上方
            if y + h > sr.bottom() - pad:
                anchor_tl = self.mapToGlobal(self.rect().topLeft())
                y = anchor_tl.y() - scale.px(6) - h
        tip._target_x = x
        tip._target_y = y

    def _show_tip(self) -> None:
        tip = self._tip
        if tip._hide_anim is not None:
            tip._hide_anim.stop()
            tip._hide_anim = None
        self._position_tip()
        tip.setVisible(True)
        tip._eff.setOpacity(0.0)
        tip._slide = 6
        tip.move(tip._target_x, tip._target_y + 6)
        dur = _dur("help_in")
        a1 = QPropertyAnimation(tip._eff, b"opacity", tip)
        a1.setDuration(dur)
        a1.setEasingCurve(_curve("help_in"))
        a1.setStartValue(0.0)
        a1.setEndValue(1.0)
        a2 = QPropertyAnimation(tip, b"slide", tip)
        a2.setDuration(dur)
        a2.setEasingCurve(_curve("help_in"))
        a2.setStartValue(6)
        a2.setEndValue(0)
        grp = QParallelAnimationGroup(tip)
        grp.addAnimation(a1)
        grp.addAnimation(a2)
        tip._show_anim = grp
        grp.start()

    def _hide_tip(self) -> None:
        tip = self._tip
        if tip is None or not tip.isVisible():
            return
        if tip._show_anim is not None:
            tip._show_anim.stop()
            tip._show_anim = None
        a = QPropertyAnimation(tip._eff, b"opacity", tip)
        a.setDuration(_dur("help_out"))
        a.setEasingCurve(_curve("help_out"))
        a.setStartValue(tip._eff.opacity())
        a.setEndValue(0.0)
        a.finished.connect(tip.hide)
        tip._hide_anim = a
        a.start()

    # -- 自绘：14px 圆 + ? --
    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = style.palette()
        d = scale.px(14)
        off = (self.width() - d) / 2.0
        rect = QRectF(off, off, d, d)
        stroke = _mix(QColor(pal["border"]), QColor(pal["border_2"]), self._hover)
        char_col = _mix(QColor(pal["text_mute"]), QColor(pal["text"]), self._hover)
        pen = QPen(stroke)
        pen.setWidthF(1.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)
        p.setPen(char_col)
        font = p.font()
        font.setPixelSize(scale.px(11))
        font.setWeight(QFont.Weight.Medium)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "?")
        p.end()


class PageHeader(QWidget):
    """页头一行：[标题 | ?图标 | 弹性留白]；说明收进 ? 的悬浮卡。"""

    def __init__(self, title: str, help_text: str = "", parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(scale.px(8))
        lay.addWidget(SubtitleLabel(title))
        self._icon = HelpIcon(help_text)
        lay.addWidget(self._icon)
        lay.addStretch(1)

    def help_icon(self) -> HelpIcon:
        return self._icon


def page_header(title: str, help_text: str = "") -> PageHeader:
    """工厂：生成带 ? 帮助图标的页头。无 help_text 时图标自动隐藏。"""
    return PageHeader(title, help_text)


# ===========================================================================
# 页头统一容器（PageHeaderBar）
# ---------------------------------------------------------------------------
# 背景：页面头部历史上分三种形态（page_header 标题行 / 裸 QTabWidget tab 条 /
# 手写 SubtitleLabel+CaptionLabel），字号层级、页头高度、内容起点各不相同，
# 且 QTabWidget::pane 自带圆角边框，导致多 tab 页内容被方框包住。
#
# 统一办法：所有页面顶层都是 [PageHeaderBar 固定 32px] + 间距 12 + 内容区。
#   - 无 tab 页：槽里放 SubtitleLabel(标题) + ?
#   - 多 tab 页：槽里放 QTabBar + ?（? 的说明随当前 tab 切换）
# 多 tab 页因此必须把 QTabWidget 拆成 QTabBar + QStackedWidget，
# 由 HeaderTabs 提供与 QTabWidget 同名的最小 API（addTab / currentChanged /
# currentIndex / setCurrentIndex / count / widget），迁移时改动量很小。
#
# 度量单一真源：PAGE_BAR_H = 32，全部走 scale.px，随字号档位同步缩放。
# ===========================================================================
PAGE_BAR_H = 32


class HeaderTabs:
    """QTabBar + QStackedWidget 组合，替代 QTabWidget。

    tab 条（.bar）交给 PageHeaderBar 收纳，内容区（.stack）留在页面主体，
    这样多 tab 页与无 tab 页能共用同一个页头容器，内容区也不再被 pane 外框包住。

    用法（替换 QTabWidget 的最小改动）：
        self.tabs = HeaderTabs()          # 原 self.tabs = QTabWidget()
        self.tabs.addTab(w, "名称")        # 同 QTabWidget
        self.tabs.currentChanged.connect(self._on_tab_changed)
        lay.addWidget(self.tabs.stack, 1) # 原 lay.addWidget(self.tabs, 1)
    """

    def __init__(self, parent=None) -> None:
        self.bar = QTabBar(parent)
        self.bar.setObjectName("pageHeaderTabBar")
        self.bar.setExpanding(False)
        self.bar.setDrawBase(False)
        self.stack = QStackedWidget(parent)
        self.bar.currentChanged.connect(self.stack.setCurrentIndex)
        # 对外暴露与 QTabWidget 同名的信号（栈切换 = tab 切换）
        self.currentChanged = self.stack.currentChanged

    def addTab(self, widget, title: str) -> int:  # noqa: N802
        self.stack.addWidget(widget)
        return self.bar.addTab(title)

    def count(self) -> int:
        return self.bar.count()

    def currentIndex(self) -> int:  # noqa: N802
        return self.bar.currentIndex()

    def setCurrentIndex(self, i: int) -> None:  # noqa: N802
        self.bar.setCurrentIndex(i)

    def widget(self, i: int):
        return self.stack.widget(i)


class PageHeaderBar(QWidget):
    """页头统一容器：固定高度盒模型，槽内可填「标题 + ?」或「tab 条 + ?」。

    两种填充模式互斥，构造后调其一：
        bar.set_title("销项发票", "说明...")
        bar.set_tabs(self.tabs, help_map={0: "说明A", 1: "说明B"})
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeaderBar")
        self.setFixedHeight(scale.px(PAGE_BAR_H))
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(scale.px(8))

    def set_title(self, text: str, help_text: str = "") -> HelpIcon:
        """无 tab 页：槽里放标题，右侧挂 ?。"""
        self._lay.addWidget(
            SubtitleLabel(text), 0, Qt.AlignmentFlag.AlignBottom)
        return self._attach_help(help_text)

    def set_tabs(self, tabs: HeaderTabs, help_map: dict | None = None) -> HelpIcon:
        """多 tab 页：槽里放 tab 条，右侧挂 ?；? 的说明随当前 tab 切换。"""
        self._lay.addWidget(tabs.bar, 0, Qt.AlignmentFlag.AlignBottom)
        icon = self._attach_help("")
        help_map = help_map or {}
        tabs.currentChanged.connect(
            lambda i: icon.set_help_text(help_map.get(i, "")))
        icon.set_help_text(help_map.get(tabs.currentIndex(), ""))
        return icon

    def _attach_help(self, text: str) -> HelpIcon:
        self._lay.addStretch(1)
        icon = HelpIcon(text)
        self._lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        return icon


def apply_page_layout(lay) -> None:
    """页面顶层布局归一：左右 24 / 上下 16 / 间距 12（全部走 scale.px）。"""
    lay.setContentsMargins(scale.px(24), scale.px(16), scale.px(24), scale.px(16))
    lay.setSpacing(scale.px(12))
