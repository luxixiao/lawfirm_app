"""Notion 风格控件垫片

把 qfluentwidgets 的"样式型"控件替换为纯 Qt 控件 + style.py 的 QSS 控制外观，
彻底去掉 Fluent 混搭。视图只需把 `from qfluentwidgets import (...)` 改成
`from app.ui.widgets import (...)`，构造调用保持不变（同名、同参数）。

保留 qfluentwidgets 仅用于 main_window 的 InfoBar toast。
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QComboBox,
    QLineEdit,
    QStyle,
    QStyleOptionViewItem,
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


class FrozenTableWidget(QTableWidget):
    """首列冻结表格：横向滚动时前 frozen 列固定不动（painter 重绘叠加）

    用法与 QTableWidget 一致，构造后调用 setFrozenColumns(n) 指定冻结列数。
    适用于列多、需要参照首列（序号/姓名/项目）的表格（如结算总表）。
    """

    def __init__(self, *args, frozen: int = 1, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._frozen = max(1, frozen)
        self.horizontalScrollBar().valueChanged.connect(lambda *_: self.viewport().update())
        self.verticalScrollBar().valueChanged.connect(lambda *_: self.viewport().update())

    def setFrozenColumns(self, n: int) -> None:
        self._frozen = max(1, n)
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
        painter = QPainter(self.viewport())
        painter.save()
        painter.setClipRect(0, 0, fw, self.viewport().height())
        first = max(0, self.rowAt(0))
        last = self.rowAt(self.viewport().height() - 1)
        if last < 0:
            last = self.rowCount() - 1
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
                self.itemDelegate().paint(painter, opt, idx)
        painter.restore()
        # 冻结列分隔线
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
