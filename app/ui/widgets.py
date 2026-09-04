"""Notion 风格控件垫片

把 qfluentwidgets 的"样式型"控件替换为纯 Qt 控件 + style.py 的 QSS 控制外观，
彻底去掉 Fluent 混搭。视图只需把 `from qfluentwidgets import (...)` 改成
`from app.ui.widgets import (...)`，构造调用保持不变（同名、同参数）。

保留 qfluentwidgets 仅用于 main_window 的 InfoBar toast。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPalette
from PySide6.QtWidgets import (
    QToolTip,
    QLabel,
    QPushButton,
    QComboBox,
    QLineEdit,
    QStyle,
    QStyleOptionViewItem,
    QTableWidget,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from app.ui import scale as _scale


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


class HelpButton(QPushButton):
    """「?」帮助按钮：鼠标进入立即弹说明气泡。

    不依赖原生 tooltip 的 ~700ms 悬停延迟（真机上存在不触发的情况），
    enterEvent 直接 QToolTip.showText，所见即所悬。
    """

    def __init__(self, parent=None) -> None:
        super().__init__("?", parent)
        self._help_text = ""

    def set_help_text(self, text: str) -> None:
        self._help_text = text or ""
        self.setToolTip(self._help_text)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self._help_text:
            pos = self.mapToGlobal(QPoint(0, self.height() + _scale.px(6)))
            QToolTip.showText(pos, self._help_text, self)
        super().enterEvent(event)


class PageHeader(QWidget):
    """单功能页统一页头：标题 + 可选「?」（说明收进 ? 的 hover 提示）。

    说明（description）不再单独占一行可视文案，而是折进标题右侧「?」图标的
    hover tooltip —— 常驻高度为 0，符合"不挤占内容空间"的要求；「?」的气泡
    文案若由独立的「? 帮助」任务注入，则走 help_key 槽位。

    外层不留边距：本组件是 SubtitleLabel / CaptionLabel 的 drop-in 替换，
    外边距由宿主页面布局统一提供，避免与页面 padding 叠加导致标题多缩进。
    """

    def __init__(self, title: str, description: str = "", help_key: str | None = None,
                 parent=None, margins: tuple | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeader")
        self._help_key = help_key
        root = QVBoxLayout(self)
        if margins:
            # 全出血页面（自身 0 边距）用 margins 指定缩进，随字号档位缩放
            root.setContentsMargins(*[_scale.px(m) for m in margins])
        else:
            root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(_scale.px(4))

        top = QHBoxLayout()
        top.setSpacing(_scale.px(8))
        self.title_label = SubtitleLabel(title)
        top.addWidget(self.title_label)
        # 「?」仅在有说明或帮助内容时出现；说明进 tooltip，不占常驻高度
        if description or help_key:
            self.help_btn = HelpButton()
            self.help_btn.setObjectName("helpBtn")
            hs = _scale.px(20)
            self.help_btn.setFixedSize(hs, hs)
            if description:
                self.help_btn.set_help_text(description)
            top.addWidget(self.help_btn)
        top.addStretch(1)
        root.addLayout(top)


class PageHint(QLabel):
    """多 tab 模块页：tab 上方的一行细说明（灰、非粗体）。仅作模块级上下文。"""

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
