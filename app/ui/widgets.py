"""Notion 风格控件垫片

把 qfluentwidgets 的"样式型"控件替换为纯 Qt 控件 + style.py 的 QSS 控制外观，
彻底去掉 Fluent 混搭。视图只需把 `from qfluentwidgets import (...)` 改成
`from app.ui.widgets import (...)`，构造调用保持不变（同名、同参数）。

保留 qfluentwidgets 仅用于 main_window 的 InfoBar toast。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QComboBox,
    QLineEdit,
    QTableWidget,
)


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

    def setBorderVisible(self, visible: bool) -> None:  # noqa: D401
        return None

    def setBorderRadius(self, radius: int) -> None:
        return None
