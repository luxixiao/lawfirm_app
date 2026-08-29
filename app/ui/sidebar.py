"""方案 C 双栏侧栏：常驻图标 Rail（一级大类）+ 右 sub panel（二级子项）。

- 左 Rail：6 个大类图标按钮（互斥高亮），底部「收起/展开」按钮。
- 右 sub panel：当前大类的标题（可点击折叠子项）+ 该大类子项按钮 + 底部挂件（皮肤切换等）。
- 收起：隐藏右栏，只留 56px 图标 Rail；收起/上次大类状态用 QSettings 持久化。
- 图标取自 qfluentwidgets 的 FluentIcon，用 getattr 防御，拼写缺失自动回退，绝不抛异常。
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QPushButton, QVBoxLayout, QWidget,
)
from qfluentwidgets import FluentIcon as FIC

RAIL_W = 56
SUB_W = 200
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
        self._collapsed = bool(self._settings.value("collapsed", False))
        self._current_group = self._group_titles[0]
        self._rail_buttons: dict[str, QPushButton] = {}
        self._item_buttons: dict[str, QPushButton] = {}
        self._bottom = bottom_widget
        self._init_ui()

        last = self._settings.value("last_group", self._group_titles[0], type=str)
        if last not in self._group_titles:
            last = self._group_titles[0]
        self.show_group(last)
        self.set_collapsed(self._collapsed)

    # ------------------------------------------------------------------ #
    # UI 构建
    # ------------------------------------------------------------------ #
    def _init_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 左 Rail（图标列）----
        self.rail = QWidget()
        self.rail.setObjectName("rail")
        self.rail.setFixedWidth(RAIL_W)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(0, 10, 0, 10)
        rail_layout.setSpacing(6)
        self._rail_group = QButtonGroup(self)
        self._rail_group.setExclusive(True)
        for title in self._group_titles:
            btn = QPushButton()
            btn.setObjectName("railBtn")
            btn.setCheckable(True)
            btn.setToolTip(title)
            btn.setIcon(_icon(GROUP_ICON.get(title, _ICON_FALLBACK)))
            btn.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
            btn.clicked.connect(lambda _checked=False, t=title: self._on_rail(t))
            self._rail_group.addButton(btn)
            self._rail_buttons[title] = btn
            rail_layout.addWidget(btn)
        rail_layout.addStretch(1)
        self.toggle_btn = QPushButton()
        self.toggle_btn.setObjectName("railToggle")
        self.toggle_btn.setToolTip("收起 / 展开侧边栏")
        self.toggle_btn.clicked.connect(self._on_toggle)
        rail_layout.addWidget(self.toggle_btn)

        # ---- 右 sub panel（子项）----
        self.sub = QWidget()
        self.sub.setObjectName("subPanel")
        self.sub.setFixedWidth(SUB_W)
        sub_layout = QVBoxLayout(self.sub)
        sub_layout.setContentsMargins(0, 0, 0, 0)
        sub_layout.setSpacing(0)

        self.header = QPushButton()
        self.header.setObjectName("subHeader")
        self.header.clicked.connect(self._on_header_toggle)
        sub_layout.addWidget(self.header)

        self.items_widget = QWidget()
        self.items_layout = QVBoxLayout(self.items_widget)
        self.items_layout.setContentsMargins(8, 4, 8, 4)
        self.items_layout.setSpacing(2)
        sub_layout.addWidget(self.items_widget)
        sub_layout.addStretch(1)

        if self._bottom is not None:
            sub_layout.addWidget(self._bottom)

        root.addWidget(self.rail)
        root.addWidget(self.sub)

    def _build_items(self, title: str) -> None:
        for btn in list(self._item_buttons.values()):
            btn.deleteLater()
        self._item_buttons.clear()
        self._item_group = QButtonGroup(self)
        self._item_group.setExclusive(True)
        items = next(items for t, items in self.nav_groups if t == title)
        for key, label in items:
            btn = QPushButton(label)
            btn.setObjectName("navItem")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, k=key: self.itemSelected.emit(k))
            self._item_group.addButton(btn)
            self._item_buttons[key] = btn
            self.items_layout.addWidget(btn)
        self.header.setText(title)
        self._current_group = title

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _on_rail(self, title: str) -> None:
        self.show_group(title)
        self.groupSelected.emit(title)

    def show_group(self, title: str) -> None:
        if title not in self._group_titles:
            return
        self._build_items(title)
        self._set_active_rail(title)
        self.items_widget.setVisible(True)
        self._settings.setValue("last_group", title)

    def _set_active_rail(self, title: str) -> None:
        btn = self._rail_buttons.get(title)
        if btn is not None:
            btn.setChecked(True)

    def set_active_group(self, title: str) -> None:
        self._set_active_rail(title)

    def activate(self, key: str, group: str) -> None:
        """由主窗口在切换页面时调用：确保右侧显示对应大类并高亮子项。"""
        if group != self._current_group:
            self.show_group(group)
        self.set_active_item(key)

    def set_active_item(self, key: str) -> None:
        btn = self._item_buttons.get(key)
        if btn is not None:
            btn.setChecked(True)

    def _on_header_toggle(self) -> None:
        self.items_widget.setVisible(not self.items_widget.isVisible())

    def _on_toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, b: bool) -> None:
        self._collapsed = bool(b)
        self.sub.setVisible(not self._collapsed)
        self._settings.setValue("collapsed", self._collapsed)
        # 展开态显示「‹」提示可收起；收起态显示「›」提示可展开
        self.toggle_btn.setText("‹" if not self._collapsed else "›")

    def key_to_group(self, key: str):
        return self._key_to_group.get(key)
