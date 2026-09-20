"""方案 A 折叠侧栏（视觉与动效改造：方案 S1，见 design/sidebar_redesign_spec.md）

- 展开（240px）：各大类纵向排列，标题行自绘「图标 + 大类名 + chevron」，点击切换子项；
  子项为该大类下的页面入口。底部挂件（偏好设置等）常驻。
- 收起（60px）：只显示大类图标（居中 + tooltip），子项与底部挂件隐藏；
  点大类图标 = 展开回宽态并展开该组子项。
- **默认自动折叠**；鼠标移入侧栏自动展开（防抖 80ms），移出自动收回（防抖 120ms）；
  展开后顶部「固定」按钮可固定为展开。
- 各组折叠状态 + 固定状态用 QSettings 持久化（pinned / collapsed_groups）。
- 子项按钮为 `NavItemButton`：在 `#navItem` QSS 之上自绘一枚右上角角标
  （`set_item_badge(key, text)`，A7「还有 N 个账期待确认入库」），
  不用子 QLabel + effect —— 理由见该类注释。
- 动效：hover 120ms、按下 80ms、大类展开 220ms（子项错峰 18ms）、收起 160ms、
  侧栏展开 240ms / 收起 180ms、chevron 180ms；全部走 `style.motion_enabled()` 开关。

图标来自 `app.ui.nav_icons`（QPainter 自绘，随状态换色），不再依赖 qfluentwidgets。
"""
from __future__ import annotations

from PySide6.QtCore import (
    Property, QEasingCurve, QParallelAnimationGroup, QPropertyAnimation,
    QRectF, QSettings, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from app.ui import scale, style
from app.ui.nav_icons import draw_chevron, draw_nav_icon

# 侧栏几何的**基准值**（= 标准档 13px 下的像素）。
# 一律经 scale.px() 派生后使用，字号档位变化后由 reapply_metrics() 统一重算。
BASE_W_EXPAND = 240
BASE_W_COLLAPSE = 60
BASE_ICON_SIZE = 20
BASE_ROW_GROUP = 34
BASE_ROW_ITEM = 31
BASE_PAD_LEFT = 8     # 大类行内左边距（容器 margin 6 + 8 = 图标左缘 14）
BASE_ICON_GAP = 8     # 图标 → 文字
BASE_CHEVRON = 12
BASE_MARGIN_SIDE = 6  # 分组区左右外边距


def _px(base: float) -> int:
    """按当前字号档位派生像素尺寸。"""
    return scale.px(base)

# 动效时长（ms）与缓动
MOTION = {
    "hover": (120, QEasingCurve.Type.OutCubic),
    "press": (80, QEasingCurve.Type.OutQuad),
    "group_in": (220, QEasingCurve.Type.OutQuart),
    "group_out": (160, QEasingCurve.Type.InOutCubic),
    "rail_in": (240, QEasingCurve.Type.OutQuart),
    "rail_out": (180, QEasingCurve.Type.OutCubic),
    "chevron": (180, QEasingCurve.Type.OutCubic),
    "label": (120, QEasingCurve.Type.OutCubic),
}


def _dur(key: str) -> int:
    """动效时长；关闭动效时降为 1ms（直接落终值，不报错）。"""
    return 1 if not style.motion_enabled() else MOTION[key][0]


def _curve(key: str) -> QEasingCurve:
    return MOTION[key][1]


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def _anim(target, prop: bytes, start, end, key: str, parent=None):
    a = QPropertyAnimation(target, prop, parent or target)
    a.setStartValue(start)
    a.setEndValue(end)
    a.setDuration(_dur(key))
    a.setEasingCurve(_curve(key))
    return a


# --------------------------------------------------------------------------- #
# 大类标题行（自绘：hover / press 插值 + chevron 旋转 + 收起态只画图标）
# --------------------------------------------------------------------------- #
class NavHeaderButton(QPushButton):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.setObjectName("groupHeaderBtn")
        self.setFixedHeight(_px(BASE_ROW_GROUP))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(title)          # 收起态纯图标列也能确认含义
        self._hover = 0.0
        self._press = 0.0
        self._angle = 0.0               # chevron 0=朝右（折叠） 90=朝下（展开）
        self._label = 1.0               # 大类文字透明度（侧栏收起时淡出）
        self._show_text = True
        self._expanded = False
        self._anim = {}

    # -- 属性（供 QPropertyAnimation 驱动） --
    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, v: float) -> None:
        self._hover = v
        self.update()

    def _get_press(self) -> float:
        return self._press

    def _set_press(self, v: float) -> None:
        self._press = v
        self.update()

    def _get_angle(self) -> float:
        return self._angle

    def _set_angle(self, v: float) -> None:
        self._angle = v
        self.update()

    def _get_label(self) -> float:
        return self._label

    def _set_label(self, v: float) -> None:
        self._label = v
        self.update()

    hoverProgress = Property(float, _get_hover, _set_hover)
    pressProgress = Property(float, _get_press, _set_press)
    angle = Property(float, _get_angle, _set_angle)
    labelAlpha = Property(float, _get_label, _set_label)

    # 动画属性 → 私有字段
    _PROP_ATTR = {"hoverProgress": "_hover", "pressProgress": "_press",
                  "angle": "_angle", "labelAlpha": "_label"}

    # -- 状态切换 --
    def _run(self, prop: str, end: float, key: str) -> None:
        old = self._anim.pop(prop, None)
        if old is not None:
            old.stop()
        start = getattr(self, self._PROP_ATTR[prop])
        a = _anim(self, prop.encode(), start, end, key)
        self._anim[prop] = a
        a.start()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self._run("hoverProgress", 1.0, "hover")

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._run("pressProgress", 0.0, "press")
        self._run("hoverProgress", 0.0, "hover")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        super().mousePressEvent(event)
        self._run("pressProgress", 1.0, "press")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self._run("pressProgress", 0.0, "press")

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._run("angle", 90.0 if expanded else 0.0, "chevron")

    def set_text_visible(self, visible: bool, animate: bool = True) -> None:
        self._show_text = bool(visible)
        if animate:
            self._run("labelAlpha", 1.0 if visible else 0.0, "label")
        else:
            self._label = 1.0 if visible else 0.0
            self.update()

    # -- 绘制 --
    def paintEvent(self, event) -> None:  # noqa: N802
        pal = style.palette()
        p = QPainter(self)
        if not p.isActive():
            # 被 QGraphicsEffect / render() 离屏重绘占用时不应再画，避免 Qt 告警
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 背景：bg_side → bg_hover（hover）→ bg_select（press）
        col = _mix(QColor(pal["bg_side"]), QColor(pal["bg_hover"]), self._hover)
        col = _mix(col, QColor(pal["bg_select"]), self._press)
        if self._hover > 0.01 or self._press > 0.01:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)

        h = self.height()
        pad = _px(BASE_PAD_LEFT)
        icon_size = _px(BASE_ICON_SIZE)
        icon_x = pad if self._show_text else (self.width() - icon_size) / 2
        icon_rect = QRectF(icon_x, (h - icon_size) / 2, icon_size, icon_size)

        # 图标与文字颜色：默认 text_mute，hover / 按下 / 展开 → text
        t = max(self._hover, self._press, 1.0 if self._expanded else 0.0)
        fg = _mix(QColor(pal["text_mute"]), QColor(pal["text"]), t)
        draw_nav_icon(p, self.title, icon_rect, fg)

        if self._label > 0.01:
            text_color = QColor(fg)
            text_color.setAlphaF(self._label)
            p.setPen(text_color)
            font = p.font()
            # 10pt ≈13px @96DPI；用浮点 pt 让侧栏文字随档位连续缩放
            font.setPointSizeF(10 * scale.ratio())
            font.setWeight(QFont.Weight.Medium if not self._expanded
                           else QFont.Weight.DemiBold)
            p.setFont(font)
            text_x = pad + icon_size + _px(BASE_ICON_GAP)
            chev_w = _px(BASE_CHEVRON) + pad
            p.drawText(QRectF(text_x, 0, self.width() - text_x - chev_w, h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       self.title)

            chev_color = _mix(QColor(pal["text_faint"]), QColor(pal["text_mute"]), self._hover)
            chev_color.setAlphaF(self._label)
            chevron = _px(BASE_CHEVRON)
            chev_rect = QRectF(self.width() - pad - chevron, (h - chevron) / 2,
                               chevron, chevron)
            p.save()
            c = chev_rect.center()
            p.translate(c)
            p.rotate(self._angle)
            p.translate(-c.x(), -c.y())
            draw_chevron(p, chev_rect, chev_color)
            p.restore()
        p.end()


# --------------------------------------------------------------------------- #
# 子项按钮（A7：右上角自绘角标）
# --------------------------------------------------------------------------- #
class NavItemButton(QPushButton):
    """侧栏子项按钮：保住 `#navItem` QSS，另在右上角自绘一枚角标。

    P0-3 探针结论（2026-09-16）：全仓**无**任何原生 badge API
    （`NavigationInterface` / `QBadgeLabel` / `InfoBadge` 均不存在），且
    **整条侧栏只允许 `GroupPanel` 那一层 `QGraphicsOpacityEffect`**（嵌套 effect
    会让 Qt 对同一 widget 再开一个 painter → "A paint device can only be painted
    by one painter at a time"，见下方 GroupPanel 注释）。
    → 角标**不能**用「子 QLabel + 自己的 effect」实现，只能像本类这样在
    `paintEvent` 里直接画；文案走 `set_badge()`，空串即隐藏。
    """

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._badge = ""

    def set_badge(self, text: str) -> None:
        """设置角标文案（空串 / None = 不显示）。"""
        t = str(text or "").strip()
        if t == self._badge:
            return
        self._badge = t
        self.update()

    def badge(self) -> str:
        return self._badge

    def paintEvent(self, event) -> None:  # noqa: N802
        # 先走基类绘制，让 `#navItem` 的底色 / 选中态 / 文字全部由 QSS 决定
        super().paintEvent(event)
        if not self._badge:
            return
        p = QPainter(self)
        if not p.isActive():
            # 被 QGraphicsEffect / render() 离屏重绘占用时不再画，避免 Qt 告警
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = style.palette()
        fm = self.fontMetrics()
        h = float(max(_px(16), fm.height()))
        pad = float(_px(6))
        w = max(h, float(fm.horizontalAdvance(self._badge)) + pad * 2)
        rect = QRectF(self.width() - w - _px(BASE_ICON_GAP),
                      (self.height() - h) / 2.0, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(pal["accent_red"]))
        p.drawRoundedRect(rect, h / 2.0, h / 2.0)
        f = p.font()
        f.setPointSizeF(max(8.0, 9.0 * scale.ratio()))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._badge)
        p.end()


# --------------------------------------------------------------------------- #
# 子项容器（高度 + 透明度动画）
#
# 重要：**整条侧栏只允许容器这一层 QGraphicsOpacityEffect**。
# 早先给每个子项按钮也挂了 effect（做错峰淡入），形成嵌套 effect——
# Qt 对带 effect 的子树做离屏重绘时会给同一 widget 再开一个 painter，
# 于是刷 "QPainter::begin: A paint device can only be painted by one painter at a time"。
# 改为只用一层；逐条出现的观感由高度增长天然产生。
# --------------------------------------------------------------------------- #
class GroupPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._open = True
        self._anim = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(_px(BASE_MARGIN_SIDE), _px(3),
                               _px(BASE_MARGIN_SIDE), _px(6))
        lay.setSpacing(3)

        self._eff = QGraphicsOpacityEffect(self)
        self._eff.setOpacity(1.0)
        self.setGraphicsEffect(self._eff)

    def add_item(self, btn: QPushButton) -> None:
        self.layout().addWidget(btn)

    def is_open(self) -> bool:
        return self._open

    def set_open(self, open_: bool, animate: bool = True) -> None:
        self._open = bool(open_)
        if not animate or not style.motion_enabled():
            self._finish(open_)
            return
        if self._anim is not None:
            self._anim.stop()
            self._anim = None

        if open_:
            # 先放开高度量出目标值，再回到起点做动画
            self.setVisible(True)
            self.setMaximumHeight(16777215)      # QWIDGETSIZE_MAX
            self.layout().activate()
            target = max(0, self.sizeHint().height())
            self.setMaximumHeight(0)
            start = 0
            key = "group_in"
        else:
            target = 0
            start = max(0, self.height())
            key = "group_out"

        grp = QParallelAnimationGroup(self)
        grp.addAnimation(_anim(self, b"maximumHeight", start, target, key, self))
        grp.addAnimation(_anim(self._eff, b"opacity",
                               0.0 if open_ else 1.0, 1.0 if open_ else 0.0, key, self))
        self._anim = grp
        grp.finished.connect(lambda: self._finish(open_))
        grp.start()

    def _finish(self, open_: bool) -> None:
        self._anim = None
        self.setMaximumHeight(16777215 if open_ else 0)
        self.setVisible(open_)
        self._eff.setOpacity(1.0 if open_ else 0.0)


# --------------------------------------------------------------------------- #
# 侧栏
# --------------------------------------------------------------------------- #
class SidebarWidget(QWidget):
    groupSelected = Signal(str)
    itemSelected = Signal(str)
    collapseToggled = Signal(bool)

    # 宽度动画属性：setter 内部走 setFixedWidth，只有一个状态量
    def _get_rail(self) -> int:
        return self.width()

    def _set_rail(self, v: int) -> None:
        self.setFixedWidth(int(v))

    railWidth = Property(int, _get_rail, _set_rail)

    def __init__(self, nav_groups, bottom_widget=None, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.nav_groups = nav_groups
        self._group_titles = [g[0] for g in nav_groups]
        self._key_to_group = {k: t for t, items in nav_groups for k, _ in items}
        self._settings = QSettings("lawfirm", "sidebar")
        self._pinned = bool(self._settings.value("pinned", False))
        self._collapsed = not self._pinned
        saved = self._settings.value("collapsed_groups", "", type=str)
        folded = {t for t in (saved.split("|") if saved else []) if t}
        self._folded = {t: (t in folded) for t in self._group_titles}
        self._header_buttons: dict[str, NavHeaderButton] = {}
        self._panels: dict[str, GroupPanel] = {}
        self._item_buttons: dict[str, NavItemButton] = {}
        self._item_group = QButtonGroup(self)
        self._item_group.setExclusive(True)
        self._bottom = bottom_widget
        self._width_anim = None
        self._enter_timer = QTimer(self)
        self._enter_timer.setSingleShot(True)
        self._enter_timer.timeout.connect(lambda: self.set_collapsed(False))
        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.timeout.connect(lambda: self.set_collapsed(True))
        self._init_ui()
        self.set_collapsed(self._collapsed, animate=False)

    # ------------------------------------------------------------------ #
    # UI 构建
    # ------------------------------------------------------------------ #
    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(10, 8, 10, 4)
        top.setSpacing(6)
        self.toggle_btn = QPushButton()
        self.toggle_btn.setObjectName("groupToggle")
        self.toggle_btn.setToolTip("收起 / 展开侧边栏")
        self.toggle_btn.clicked.connect(self._on_toggle)
        self.pin_btn = QPushButton("固定")
        self.pin_btn.setObjectName("groupPin")
        self.pin_btn.setCheckable(True)
        self.pin_btn.setToolTip("固定为展开（鼠标移开也不收起）")
        self.pin_btn.clicked.connect(self._on_pin)
        top.addWidget(self.toggle_btn)
        top.addWidget(self.pin_btn)
        top.addStretch(1)
        root.addLayout(top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("scrollContent")
        self.groups_layout = QVBoxLayout(container)
        self.groups_layout.setContentsMargins(_px(BASE_MARGIN_SIDE), _px(4),
                                              _px(BASE_MARGIN_SIDE), _px(4))
        self.groups_layout.setSpacing(_px(2))
        for title, items in self.nav_groups:
            self._add_group(title, items)
        self.groups_layout.addStretch(1)
        self.scroll.setWidget(container)
        root.addWidget(self.scroll, 1)

        # 底部挂件直接显隐，不再叠 effect（effect 只在 GroupPanel 用一层）
        if self._bottom is not None:
            root.addWidget(self._bottom)

    def _add_group(self, title: str, items) -> None:
        header = NavHeaderButton(title)
        header.clicked.connect(lambda _checked=False, t=title: self._on_header(t))
        self._header_buttons[title] = header
        self.groups_layout.addWidget(header)

        panel = GroupPanel()
        for key, label in items:
            # A7：子项用 NavItemButton（多一层自绘角标能力），其余行为与 QPushButton 一致
            btn = NavItemButton(label)
            btn.setObjectName("navItem")
            btn.setMinimumHeight(_px(BASE_ROW_ITEM))
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, k=key: self.itemSelected.emit(k))
            self._item_group.addButton(btn)
            self._item_buttons[key] = btn
            panel.add_item(btn)
        self._panels[title] = panel
        self.groups_layout.addWidget(panel)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _on_header(self, title: str) -> None:
        """点标题：收起态先展开整体并展开该组；展开态切换该组子项。"""
        if self._collapsed:
            self.set_collapsed(False)
            self._folded[title] = False
            self._apply_fold()
            self._save_folded()
            return
        self._folded[title] = not self._folded[title]
        self._apply_fold()
        self._save_folded()
        self.groupSelected.emit(title)

    def _apply_fold(self, animate: bool = True) -> None:
        for title, panel in self._panels.items():
            want = (not self._collapsed) and (not self._folded[title])
            if panel.is_open() != want:
                panel.set_open(want, animate=animate)
            self._header_buttons[title].set_expanded(want)

    def _save_folded(self) -> None:
        folded = "|".join(t for t in self._group_titles if self._folded[t])
        self._settings.setValue("collapsed_groups", folded)

    def _on_toggle(self) -> None:
        self._enter_timer.stop()
        self._leave_timer.stop()
        self.set_collapsed(not self._collapsed)

    def _on_pin(self, checked: bool) -> None:
        self._pinned = bool(checked)
        self._settings.setValue("pinned", self._pinned)
        if self._pinned:
            self._leave_timer.stop()
            self.set_collapsed(False)

    def enterEvent(self, event) -> None:  # noqa: N802
        """鼠标移入：未固定时延迟 80ms 自动展开（防抖，快速掠过不触发）。"""
        super().enterEvent(event)
        self._leave_timer.stop()
        if not self._pinned:
            self._enter_timer.start(80)

    def leaveEvent(self, event) -> None:  # noqa: N802
        """鼠标移出：未固定时延迟 120ms 自动收回。"""
        super().leaveEvent(event)
        self._enter_timer.stop()
        if not self._pinned:
            self._leave_timer.start(120)

    # ------------------------------------------------------------------ #
    # 整体收起 / 展开
    # ------------------------------------------------------------------ #
    def set_collapsed(self, b: bool, animate: bool = True) -> None:
        self._collapsed = bool(b)
        target = _px(BASE_W_COLLAPSE) if self._collapsed else _px(BASE_W_EXPAND)
        if animate:
            self._animate_width(target)
        else:
            self._finish_width(target)
        for btn in self._header_buttons.values():
            btn.set_text_visible(not self._collapsed, animate=animate)
        if self._bottom is not None:
            self._bottom.setVisible(not self._collapsed)
        self.toggle_btn.setText("›" if self._collapsed else "‹")
        self.pin_btn.setVisible(not self._collapsed)
        self.pin_btn.setChecked(self._pinned)
        self._apply_fold(animate=animate)
        self._settings.setValue("collapsed", self._collapsed)
        self.collapseToggled.emit(self._collapsed)

    def _animate_width(self, target: int) -> None:
        if not style.motion_enabled():
            self._finish_width(target)
            return
        if self._width_anim is not None:
            self._width_anim.stop()
            self._width_anim = None
        # 只动一个自定义属性（内部 setFixedWidth），不再动 minimumWidth/maximumWidth：
        # 那两条属性动画会让侧栏在动画期间同时持有不同的 min/max 并参与父布局的
        # minimumSizeHint 计算，诱发主窗口几何反复重算。
        key = "rail_out" if self._collapsed else "rail_in"
        a = QPropertyAnimation(self, b"railWidth", self)
        a.setStartValue(self.width())
        a.setEndValue(target)
        a.setDuration(_dur(key))
        a.setEasingCurve(_curve(key))
        a.finished.connect(lambda: self._finish_width(target))
        self._width_anim = a
        a.start()

    def _finish_width(self, target: int) -> None:
        self._width_anim = None
        self.setFixedWidth(target)

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    def activate(self, key: str, group: str) -> None:
        """由主窗口切页时调用：确保整体展开、该组子项可见并高亮子项。"""
        if self._collapsed:
            self._enter_timer.stop()
            self.set_collapsed(False)
        if self._folded.get(group):
            self._folded[group] = False
            self._apply_fold()
            self._save_folded()
        self.set_active_item(key)

    def set_active_item(self, key: str) -> None:
        btn = self._item_buttons.get(key)
        if btn is not None:
            btn.setChecked(True)

    def set_item_badge(self, key: str, text: str) -> None:
        """设置某个子项右上角的角标（A7）。

        文案为空即隐藏。收起态下子项本就整体隐藏，故无需额外处理；
        已知局限：折叠（收起）状态下角标不可见，仅在侧栏展开时可见。
        """
        btn = self._item_buttons.get(key)
        if isinstance(btn, NavItemButton):
            btn.set_badge(text)

    def set_active_group(self, title: str) -> None:
        """无独立 Rail，接口保留（展开该组以便定位）。"""
        if title in self._folded and self._folded[title]:
            self._folded[title] = False
            self._apply_fold()
            self._save_folded()

    def key_to_group(self, key: str):
        return self._key_to_group.get(key)

    def reapply_metrics(self) -> None:
        """字号档位变化后重算侧栏几何（行高 / 子项高 / 分组边距 / 整体宽度）。

        与 apply_motion_pref 不同：这里只改尺寸，不动折叠状态、不做动画。
        自绘的大类行字号由 paintEvent 每次现取 scale.ratio()，无需在此处理。
        """
        for btn in self._header_buttons.values():
            btn.setFixedHeight(_px(BASE_ROW_GROUP))
        for btn in self._item_buttons.values():
            btn.setMinimumHeight(_px(BASE_ROW_ITEM))
        margin = _px(BASE_MARGIN_SIDE)
        self.groups_layout.setContentsMargins(margin, _px(4), margin, _px(4))
        self.groups_layout.setSpacing(_px(2))
        for panel in self._panels.values():
            lay = panel.layout()
            if lay is not None:
                lay.setContentsMargins(margin, _px(3), margin, _px(6))
        self._finish_width(_px(BASE_W_COLLAPSE) if self._collapsed
                           else _px(BASE_W_EXPAND))
        self.update()

    def apply_motion_pref(self) -> None:
        """动效开关变化后调用：立即落位，避免残留半透明/半高状态。"""
        self._finish_width(_px(BASE_W_COLLAPSE) if self._collapsed
                           else _px(BASE_W_EXPAND))
        self.set_collapsed(self._collapsed, animate=False)
