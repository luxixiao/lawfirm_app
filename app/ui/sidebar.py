"""方案 A 折叠侧栏：单列分组，大类标题行（图标+文字）可独立折叠子项，整体可收起为图标列。

- 展开（240px）：各大类纵向排列，标题行显示「图标 + 大类名」，点击标题切换该组子项显示；
  子项为该大类下的页面入口。底部挂件（皮肤切换等）常驻。
- 收起（60px）：只显示大类图标（文字清空、图标居中），子项与底部挂件隐藏；
  点大类图标 = 展开回宽态并展开该组子项。
- **默认自动折叠**（收起为 60px 图标列）；鼠标移入侧栏自动展开，移出自动收回；
  展开后顶部出现「固定」按钮，按下即固定为展开（鼠标移开也不收起）。
- 各组子项折叠状态 + 固定状态用 QSettings 持久化（pinned / collapsed_groups）。
- 图标取自 qfluentwidgets 的 FluentIcon，用 getattr 防御，拼写缺失自动回退，绝不抛异常。
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Signal, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)
from qfluentwidgets import FluentIcon as FIC

W_EXPAND = 240
W_COLLAPSE = 60
ICON_SIZE = 22

# 大类 -> FluentIcon 枚举名（用 getattr 防御，避免拼写错误导致崩溃）
GROUP_ICON = {
    "数据导入": "DOWNLOAD",
    "台账查看": "FOLDER",
    "业务数据": "CHART",
    "工资个税": "MONEY",
    "各类报表": "REPORT",
    "数据维护": "SETTING",
}
_ICON_FALLBACK = "DOCUMENT"

# 收起态：图标居中（内联样式优先级高于全局 QSS）
_ICON_ONLY_QSS = "text-align: center; padding-left: 0; padding-right: 0;"


def _icon(name: str) -> QIcon:
    member = getattr(FIC, name, None) or getattr(FIC, _ICON_FALLBACK, None)
    if member is None:
        return QIcon()
    try:
        return member.icon()
    except Exception:  # noqa: BLE001 - 任何图标构造失败都降级为空图标
        return QIcon()


class SidebarWidget(QWidget):
    groupSelected = Signal(str)
    itemSelected = Signal(str)
    collapseToggled = Signal(bool)

    def __init__(self, nav_groups, bottom_widget=None, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.nav_groups = nav_groups
        self._group_titles = [g[0] for g in nav_groups]
        self._key_to_group = {k: t for t, items in nav_groups for k, _ in items}
        self._settings = QSettings("lawfirm", "sidebar")
        # 固定状态：按下「固定」后保持展开，鼠标移开也不收起（持久化）
        self._pinned = bool(self._settings.value("pinned", False))
        # 默认自动折叠：未固定时收起为图标列，已固定则展开
        self._collapsed = not self._pinned
        # 各组子项折叠状态（持久化时用 | 拼接被折叠的组名）
        saved = self._settings.value("collapsed_groups", "", type=str)
        folded = {t for t in (saved.split("|") if saved else []) if t}
        self._folded = {t: (t in folded) for t in self._group_titles}
        self._header_buttons: dict[str, QPushButton] = {}
        self._items_widgets: dict[str, QWidget] = {}
        self._item_buttons: dict[str, QPushButton] = {}
        self._item_group = QButtonGroup(self)
        self._item_group.setExclusive(True)
        self._bottom = bottom_widget
        self._init_ui()
        self.set_collapsed(self._collapsed)

    # ------------------------------------------------------------------ #
    # UI 构建
    # ------------------------------------------------------------------ #
    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部按钮行：收起/展开 + 固定
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

        # 分组区（可滚动，避免 6 组 18 项在小窗口下溢出）
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("scrollContent")
        self.groups_layout = QVBoxLayout(container)
        self.groups_layout.setContentsMargins(8, 4, 8, 4)
        self.groups_layout.setSpacing(2)
        for title, items in self.nav_groups:
            self._add_group(title, items)
        self.groups_layout.addStretch(1)
        self.scroll.setWidget(container)
        root.addWidget(self.scroll, 1)

        if self._bottom is not None:
            root.addWidget(self._bottom)

    def _add_group(self, title: str, items) -> None:
        header = QPushButton(title)
        header.setObjectName("groupHeaderBtn")
        header.setIcon(_icon(GROUP_ICON.get(title, _ICON_FALLBACK)))
        header.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        header.clicked.connect(lambda _checked=False, t=title: self._on_header(t))
        self._header_buttons[title] = header
        self.groups_layout.addWidget(header)

        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 0, 0, 6)
        lay.setSpacing(2)
        for key, label in items:
            btn = QPushButton(label)
            btn.setObjectName("navItem")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, k=key: self.itemSelected.emit(k))
            self._item_group.addButton(btn)
            self._item_buttons[key] = btn
            lay.addWidget(btn)
        self._items_widgets[title] = box
        self.groups_layout.addWidget(box)

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

    def _apply_fold(self) -> None:
        for title, box in self._items_widgets.items():
            box.setVisible((not self._collapsed) and (not self._folded[title]))

    def _save_folded(self) -> None:
        folded = "|".join(t for t in self._group_titles if self._folded[t])
        self._settings.setValue("collapsed_groups", folded)

    def _on_toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def _on_pin(self, checked: bool) -> None:
        """按下「固定」= 固定为展开；取消固定 = 回到「移入展开 / 移出收回」模式。"""
        self._pinned = bool(checked)
        self._settings.setValue("pinned", self._pinned)
        if self._pinned:
            self.set_collapsed(False)

    def enterEvent(self, event) -> None:  # noqa: N802
        """鼠标移入：未固定时自动展开。"""
        super().enterEvent(event)
        if not self._pinned:
            self.set_collapsed(False)

    def leaveEvent(self, event) -> None:  # noqa: N802
        """鼠标移出：未固定时自动收回。"""
        super().leaveEvent(event)
        if not self._pinned:
            self.set_collapsed(True)

    def set_collapsed(self, b: bool) -> None:
        """整体收起 / 展开：切宽度、标题文字、子项与底部挂件可见性。"""
        self._collapsed = bool(b)
        self.setFixedWidth(W_COLLAPSE if self._collapsed else W_EXPAND)
        # 收起态：标题文字清空 + 图标居中；展开态：恢复组名与普通对齐
        for title, btn in self._header_buttons.items():
            btn.setText("" if self._collapsed else title)
            btn.setStyleSheet(_ICON_ONLY_QSS if self._collapsed else "")
        if self._bottom is not None:
            self._bottom.setVisible(not self._collapsed)
        self.toggle_btn.setText("›" if self._collapsed else "‹")
        # 「固定」按钮仅在展开态出现（收起态 60px 放不下）
        self.pin_btn.setVisible(not self._collapsed)
        self.pin_btn.setChecked(self._pinned)
        self._apply_fold()
        self._settings.setValue("collapsed", self._collapsed)
        self.collapseToggled.emit(self._collapsed)

    def activate(self, key: str, group: str) -> None:
        """由主窗口切页时调用：确保整体展开、该组子项可见并高亮子项。"""
        if self._collapsed:
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

    def set_active_group(self, title: str) -> None:
        """方案 A 无独立 Rail，接口保留（展开该组以便定位）。"""
        if title in self._folded and self._folded[title]:
            self._folded[title] = False
            self._apply_fold()
            self._save_folded()

    def key_to_group(self, key: str):
        return self._key_to_group.get(key)
