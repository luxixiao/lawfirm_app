"""通用表格交互增强（跨所有表格复用）

集中实现 5 项统一的表格行为，避免每个视图各写一遍：

1. 像素级滚动（横向/纵向 ScrollPerPixel）
2. 单元格内容被压缩（省略）时，悬停显示完整内容（tooltip）
3. 单元格被标红/标绿后，选中行时依然保持原前景色（不被反白成白字）
4. 右键表头按列筛选（隐藏不匹配行）

用法：
- 表格类（widgets.TableWidget / FrozenTableWidget）已在 __init__ 自动调用 install_common_features
- 普通 QTableWidget 显示类表格，构造后调用：
      from app.ui.table_features import install_common_features, install_header_filter
      install_common_features(self.table)
      install_header_filter(self.table)
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontMetrics, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QVBoxLayout,
)


class TableBehaviorDelegate(QStyledItemDelegate):
    """统一单元格绘制委托：

    - initStyleOption：选中（高亮）状态下，把 HighlightedText 设为单元格自身前景色，
      使被标红/标绿的单元格在选中后仍保持原色（req4）。
    - helpEvent：仅当文本被压缩（显示宽度不足以容纳）时，悬停弹出完整文本 tooltip（req2）。
    """

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:
        super().initStyleOption(option, index)
        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(fg, QColor) and fg.isValid():
            option.palette.setColor(QPalette.ColorRole.HighlightedText, fg)

    def helpEvent(self, event, view, option, index) -> bool:
        if not index.isValid() or event is None:
            return False
        val = index.data(Qt.ItemDataRole.DisplayRole)
        if val is None:
            return False
        text = str(val)
        if not text:
            return False
        item = view.itemFromIndex(index) if isinstance(view, QTableWidget) else None
        font = item.font() if item is not None else view.font()
        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(text)
        # 单元格可用宽度（去掉左右内边距与焦点框留白，约 12px）
        available = option.rect.width() - 12
        if text_w > available:
            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(event.globalPos(), text, view)
            return True
        return False


def install_common_features(table: QTableWidget) -> None:
    """像素级滚动 + 选中保色 + 悬停全文（对所有表格统一生效）。

    幂等：已安装过 TableBehaviorDelegate 则跳过，避免覆盖可能存在的
    自定义全局委托（列级委托由 setItemDelegateForColumn 优先级更高，不受影响）。
    """
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    if not isinstance(table.itemDelegate(), TableBehaviorDelegate):
        table.setItemDelegate(TableBehaviorDelegate(table))


def install_header_filter(table: QTableWidget) -> None:
    """右键表头按列筛选：列出该列唯一值，勾选后隐藏不匹配的行（req1）。

    适用于任意 QTableWidget 显示类表格；基于单元格文本过滤，与数据来源无关。
    """
    hdr = table.horizontalHeader()
    if hdr is None:
        return
    hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    # 仅保留本模块安装的筛选菜单，避免重复连接
    try:
        hdr.customContextMenuRequested.disconnect()
    except Exception:
        pass
    hdr.customContextMenuRequested.connect(
        lambda pos: _header_filter_menu(table, pos)
    )


def _header_filter_menu(table: QTableWidget, pos) -> None:
    hdr = table.horizontalHeader()
    col = hdr.columnAt(pos.x())
    if col < 0:
        return
    nrows = table.rowCount()
    uniq: list = []
    seen: set = set()
    for r in range(nrows):
        it = table.item(r, col)
        v = it.text() if it is not None else ""
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    if not uniq:
        return
    uniq.sort(key=lambda s: (len(s), s))

    dlg = QDialog(table.window() if table.window() else table)
    title = table.horizontalHeaderItem(col)
    dlg.setWindowTitle(f"按列筛选：{title.text() if title else ''}")
    dlg.resize(300, 420)
    lay = QVBoxLayout(dlg)
    lst = QListWidget()
    for v in uniq:
        it = QListWidgetItem(v)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(Qt.CheckState.Checked)
        lst.addItem(it)
    lay.addWidget(lst, 1)

    btns = QHBoxLayout()
    chk = QCheckBox("全选")
    chk.setChecked(True)
    btns.addWidget(chk)
    btns.addStretch()
    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    box.accepted.connect(dlg.accept)
    box.rejected.connect(dlg.reject)
    btns.addWidget(box)
    lay.addLayout(btns)

    def _toggle(state: int) -> None:
        for i in range(lst.count()):
            lst.item(i).setCheckState(
                Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
            )

    chk.stateChanged.connect(_toggle)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return
    sel = {
        lst.item(i).text()
        for i in range(lst.count())
        if lst.item(i).checkState() == Qt.CheckState.Checked
    }
    if len(sel) == len(uniq):
        # 全选 = 不筛选
        for r in range(nrows):
            table.setRowHidden(r, False)
        return
    for r in range(nrows):
        it = table.item(r, col)
        v = it.text() if it is not None else ""
        table.setRowHidden(r, v not in sel)
