"""通用表格交互增强（跨所有表格复用）

集中实现表格行为，避免每个视图各写一遍：

1. 像素级滚动（横向/纵向 ScrollPerPixel）
2. 单元格内容被压缩（省略）时，悬停显示完整内容（tooltip）
3. 单元格被标红/标绿后，选中行时依然保持原前景色（不被反白成白字）
4. 右键表头多重筛选（统一引擎：多列叠加 + 跨列模糊搜索，AND 组合）
5. 筛选列在表头显示漏斗标记（▾）区分

用法：
- 表格类（widgets.TableWidget / FrozenTableWidget）已在 __init__ 自动调用 install_common_features
- 普通 QTableWidget 显示类表格，构造后调用：
      from app.ui.table_features import install_common_features, install_header_filter
      install_common_features(self.table)
      self._filter = install_header_filter(self.table)   # 返回 TableFilter，可接搜索框
- 搜索框并入：self._filter.bind_search_widget(search_edit); search_edit.textChanged.connect(self._filter.set_search)
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QIcon, QPalette, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton,
    QStyledItemDelegate, QStyleOptionViewItem, QTableWidget, QVBoxLayout,
)


# 冻结列底色（偏灰），用于与常规列可视区分
_FROZEN_BG = QColor("#EAEAEA")


def set_frozen_columns(table: "QTableWidget", frozen_logical) -> None:
    """把冻结列的逻辑列号集合写入表格的单元格绘制委托，使其铺灰底。

    仅当表格已安装 TableBehaviorDelegate 时生效；其余表格（未装通用增强）忽略。
    """
    d = table.itemDelegate()
    if isinstance(d, TableBehaviorDelegate):
        d.frozen_cols = frozenset(frozen_logical)


class TableBehaviorDelegate(QStyledItemDelegate):
    """统一单元格绘制委托：

    - initStyleOption：选中（高亮）状态下，把 HighlightedText 设为单元格自身前景色，
      使被标红/标绿的单元格在选中后仍保持原色（req4）。
    - helpEvent：仅当文本被压缩（显示宽度不足以容纳）时，悬停弹出完整文本 tooltip（req2）。
    - paint：冻结列（frozen_cols）统一铺一层偏灰底色，使冻结列与常规列可视区分。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.frozen_cols = frozenset()

    def paint(self, painter, option, index) -> None:
        if index.column() in self.frozen_cols:
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_FROZEN_BG)
            painter.drawRect(option.rect)
            painter.restore()
        super().paint(painter, option, index)

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:
        super().initStyleOption(option, index)
        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        # setForeground 实际存入的是 QBrush（QColor 会被包装），故需同时处理 QBrush。
        # 选中（高亮）状态下把 HighlightedText 设为单元格自身前景色，红/绿字选中后仍保持原色。
        if isinstance(fg, QBrush) and fg.color().isValid():
            option.palette.setColor(QPalette.ColorRole.HighlightedText, fg.color())
        elif isinstance(fg, QColor) and fg.isValid():
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


# ---------------------------------------------------------------------------
# 统一多重筛选引擎
# ---------------------------------------------------------------------------

FUNNEL_MARK = " ▾"  # 兼容常量（保留，实际改用图标标记，避免污染表头标题文本）


def _funnel_icon() -> QIcon:
    """预生成漏斗图标（蓝色下三角），用于标记已设筛选的列；缓存复用。"""
    global _FUNNEL_ICON
    if _FUNNEL_ICON is None:
        pm = QPixmap(14, 14)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2D7DD2"))
        from PySide6.QtCore import QPoint
        p.drawPolygon([QPoint(2, 3), QPoint(12, 3), QPoint(7, 11)])
        p.end()
        _FUNNEL_ICON = QIcon(pm)
    return _FUNNEL_ICON


_FUNNEL_ICON = None


def _cell_key(v) -> str:
    """单元格值归一化为可比较字符串（float 用 :g，None 视为空）。"""
    if isinstance(v, float):
        return f"{v:g}"
    return "" if v is None else str(v)


class TableFilter:
    """统一多重筛选引擎：有序多列筛选 + 跨列模糊搜索，二者 AND 组合。

    - 控件模式（table 绑定）：直接隐藏行（纯 QTableWidget 显示类表格）。
    - 数据模式（table=None + reapply_cb）：调用方在渲染前用 filter_rows 过滤行数据。
    状态按设置先后保序（OrderedDict）；清除单列 / 清除全部均支持。
    """

    def __init__(self, table: Optional[QTableWidget] = None, reapply_cb: Optional[Callable] = None) -> None:
        self._table = table
        self._reapply = reapply_cb
        self._col_filters: "OrderedDict[int, set]" = OrderedDict()
        self._search = ""
        self._search_widget = None

    # ---- 状态 ----
    def set_col_filter(self, col: int, values) -> None:
        vals = {str(v) for v in values}
        if vals:
            self._col_filters[col] = vals
        else:
            self._col_filters.pop(col, None)
        self._changed()

    def clear_col(self, col: int) -> None:
        self._col_filters.pop(col, None)
        self._changed()

    def clear_all(self) -> None:
        self._col_filters.clear()
        self._search = ""
        if self._search_widget is not None:
            self._search_widget.blockSignals(True)
            self._search_widget.clear()
            self._search_widget.blockSignals(False)
        self._changed()

    def set_search(self, text: str) -> None:
        self._search = (text or "").strip()
        self._changed()

    def bind_search_widget(self, w) -> None:
        self._search_widget = w

    def active_cols(self) -> List[int]:
        return list(self._col_filters.keys())

    def has_any(self) -> bool:
        return bool(self._col_filters) or bool(self._search)

    def has_col_filters(self) -> bool:
        """仅判断按列筛选是否生效（不含跨列搜索，因搜索非按列筛选）。"""
        return bool(self._col_filters)

    def _changed(self) -> None:
        if self._reapply is not None:
            self._reapply()
        elif self._table is not None:
            self.apply_to_table(self._table)

    # ---- 数据模式：过滤行数据（BaseTableView 在渲染前调用） ----
    def filter_rows(self, rows, keyf=_cell_key):
        s = self._search.lower()
        out = []
        for row in rows:
            if s:
                hit = False
                for v in row:
                    if s in keyf(v).lower():
                        hit = True
                        break
                if not hit:
                    continue
            ok = True
            for col, vals in self._col_filters.items():
                if keyf(row[col]) not in vals:
                    ok = False
                    break
            if ok:
                out.append(row)
        return out

    # ---- 控件模式：隐藏行（纯 QTableWidget） ----
    def apply_to_table(self, table: QTableWidget) -> None:
        hdr = table.horizontalHeader()
        n = table.rowCount()
        s = self._search.lower()
        cols = list(self._col_filters.keys())
        for r in range(n):
            keep = True
            if s:
                parts = []
                for c in range(table.columnCount()):
                    vc = hdr.visualIndex(c)
                    it = table.item(r, vc) if vc >= 0 else None
                    parts.append(it.text() if it is not None else "")
                if s not in " ".join(parts).lower():
                    keep = False
            if keep:
                for col in cols:
                    vc = hdr.visualIndex(col)
                    it = table.item(r, vc) if vc >= 0 else None
                    v = it.text() if it is not None else ""
                    if v not in self._col_filters[col]:
                        keep = False
                        break
            table.setRowHidden(r, not keep)
        self._mark_headers(table)

    def _mark_headers(self, table: QTableWidget) -> None:
        """已设筛选的列在表头显示漏斗图标（不改动标题文本，避免污染列布局 key）。

        注意：筛选键是逻辑列号；表头项须按视觉列号取，故用 visualIndex 转换，
        否则列被重排/隐藏后漏斗图标会落在错误的列上。
        """
        hdr = table.horizontalHeader()
        active = set(self._col_filters.keys())
        icon = _funnel_icon()
        for c in range(table.columnCount()):
            it = table.horizontalHeaderItem(c)
            if it is not None:
                it.setIcon(QIcon())
        for logical in active:
            v = hdr.visualIndex(logical)
            if v < 0:
                continue
            it = table.horizontalHeaderItem(v)
            if it is not None:
                it.setIcon(icon)


class ColumnFilterDialog(QDialog):
    """按列筛选弹窗：唯一值勾选列表 + 顶部搜索框（实时过滤候选）+ 全选/清空。"""

    def __init__(self, parent, title: str, values, checked) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"筛选：{title}")
        self.resize(320, 460)
        self._all = sorted(values, key=lambda s: (len(s), s))
        self._checked = set(checked)
        self._displayed: List[str] = []
        lay = QVBoxLayout(self)
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索筛选值…")
        lay.addWidget(self._search)
        self._list = QListWidget()
        lay.addWidget(self._list, 1)
        bar = QHBoxLayout()
        b_all = QPushButton("全选")
        b_none = QPushButton("清空")
        bar.addWidget(b_all)
        bar.addWidget(b_none)
        bar.addStretch()
        lay.addLayout(bar)
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._loading = True
        self._populate(self._all)
        self._loading = False
        self._list.itemChanged.connect(self._on_toggle)
        self._search.textChanged.connect(self._on_search)
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none.clicked.connect(lambda: self._set_all(False))

    def _populate(self, vals) -> None:
        self._loading = True
        self._list.clear()
        self._displayed = list(vals)
        for v in vals:
            it = QListWidgetItem(v)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(
                Qt.CheckState.Checked if v in self._checked else Qt.CheckState.Unchecked
            )
            self._list.addItem(it)
        self._loading = False

    def _on_search(self, text) -> None:
        t = (text or "").strip().lower()
        self._populate(self._all if not t else [v for v in self._all if t in v.lower()])

    def _on_toggle(self, item) -> None:
        if self._loading:
            return
        v = item.text()
        if item.checkState() == Qt.CheckState.Checked:
            self._checked.add(v)
        else:
            self._checked.discard(v)

    def _set_all(self, state) -> None:
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(
                Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
            )

    def selected(self) -> set:
        # 仅返回「当前可见（受弹窗内搜索过滤后的列表）且已勾选」的值。
        # 否则搜索隐藏的项仍计入 _checked，会让 selected()==全部唯一值，
        # 触发「全选=不筛选」而把刚设的筛选清空（即用户反馈的“用了搜索筛选不生效”）。
        return set(self._displayed) & self._checked


def _column_values(table: QTableWidget, logical: int) -> List[str]:
    """收集某逻辑列的候选唯一值，用于「按列筛选」弹窗。

    注意：QTableWidget 按「视觉列号」存取单元格，故必须先用 visualIndex
    把逻辑列号换算成视觉列号，否则列被拖拽重排后取到的是错误列的数据，
    导致列筛选与搜索叠加时匹配错乱（表现为交集变空）。
    """
    hdr = table.horizontalHeader()
    vcol = hdr.visualIndex(logical)
    uniq: List[str] = []
    seen: set = set()
    if vcol < 0:
        return uniq
    for r in range(table.rowCount()):
        it = table.item(r, vcol)
        v = it.text() if it is not None else ""
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def open_col_filter_dialog(tf: TableFilter, table: QTableWidget, logical: int) -> None:
    """打开某列的按列筛选弹窗并应用结果。

    始终按所选值设置筛选；不再使用「全选 = 不筛选」自动清除。
    原因：uniq 只是「当前已显示」(可能已被跨列搜索/其它列筛选缩窄)的子集，
    选满当前子集就清除会让先前叠加的筛选/搜索失效，导致不应出现的行重新出现
    （例如按发票号码筛选时，某行发票号不含关键字但其金额/购方含关键字，
    被跨列搜索带入视图；此时选满子集误清除列筛选会让该发票号重新出现）。
    空选择时 set_col_filter 内部已自动 clear_col，无需特判。
    """
    hdr = table.horizontalHeader()
    vcol = hdr.visualIndex(logical)
    title = table.horizontalHeaderItem(vcol) if vcol >= 0 else None
    title_text = title.text() if title else ""
    uniq = _column_values(table, logical)
    cur = tf._col_filters.get(logical)
    dlg = ColumnFilterDialog(
        table.window() if table.window() else table,
        title_text, uniq, cur if cur is not None else set(uniq),
    )
    if dlg.exec() == QDialog.DialogCode.Accepted:
        sel = dlg.selected()
        tf.set_col_filter(logical, sel)


def populate_filter_menu(menu, tf: TableFilter, table: QTableWidget, pos) -> None:
    """把按列筛选的 3 个操作直接加进现有右键菜单（扁平、不嵌套），并按条件显隐：

    - 筛选此列…：恒显示
    - 清除此列筛选：仅当该列已设筛选
    - 清除全部筛选：仅当存在任意列筛选（搜索不计入，因搜索非按列筛选）
    """
    hdr = table.horizontalHeader()
    logical = hdr.logicalIndexAt(pos.x())
    if logical < 0:
        return
    act_filter = menu.addAction("筛选此列…")
    act_filter.triggered.connect(lambda: open_col_filter_dialog(tf, table, logical))
    if logical in tf._col_filters:
        act_cc = menu.addAction("清除此列筛选")
        act_cc.triggered.connect(lambda: tf.clear_col(logical))
    if tf.has_col_filters():
        act_ca = menu.addAction("清除全部筛选")
        act_ca.triggered.connect(lambda: tf.clear_all())


def open_filter_submenu(tf: TableFilter, table: QTableWidget, pos) -> None:
    """独立（无列布局合并）表格的表头右键筛选：直接弹出 3 个选项（扁平）。"""
    menu = QMenu(table)
    populate_filter_menu(menu, tf, table, pos)
    menu.exec(table.mapToGlobal(pos))


class _FilterMenuSlot:
    """表头右键筛选槽（供 column_layout 合并进「列设置…」菜单）。"""

    def __init__(self, tf: TableFilter) -> None:
        self._tf = tf

    def __call__(self, pos) -> None:
        open_filter_submenu(self._tf, self._tf._table, pos)


def install_header_filter(table: QTableWidget, reapply_cb: Optional[Callable] = None) -> TableFilter:
    """在表头启用右键多重筛选（统一引擎）。返回 TableFilter 实例（可接搜索框）。

    须在 install_column_layout 之前调用，使其「按列筛选…」并入同一右键菜单。
    幂等：重复调用同一张表不会重复连接。
    """
    hdr = table.horizontalHeader()
    if hdr is None:
        return TableFilter(table=table, reapply_cb=reapply_cb)
    # 若该表头已安装过本模块的筛选槽，先断开旧连接再重建，避免重复菜单
    old = getattr(hdr, "_tf_filter_slot", None)
    if old is not None:
        try:
            hdr.customContextMenuRequested.disconnect(old)
        except Exception:  # noqa: BLE001
            pass
    hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    tf = TableFilter(table=table, reapply_cb=reapply_cb)
    slot = _FilterMenuSlot(tf)
    hdr.customContextMenuRequested.connect(slot)
    hdr._tf_filter_slot = slot
    hdr._tf_filter_tf = tf
    hdr._tf_filter_installed = True
    return tf
