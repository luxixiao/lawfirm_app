"""通用表格视图：Fluent 筛选栏 + 表格 + 导出 Excel"""
from __future__ import annotations

from typing import Callable, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QVBoxLayout, QWidget

from app.ui import scale
from app.ui.widgets import ComboBox, LineEdit, PrimaryPushButton, PushButton, TableWidget
from app.ui.column_layout import ColumnLayoutManager
from app.ui.table_features import (
    TableFilter, open_filter_submenu, _cell_key, _funnel_icon,
    install_common_features, add_sort_actions, populate_filter_menu,
)

RED = QColor("#C0392B")    # 红字/负数
GREEN = QColor("#1E8449")  # 已收


def auto_fit_columns(table, max_width: int = 300, min_width: int = 70) -> None:
    """列宽自适应：按内容撑开但限幅，最后一列拉伸，避免文字被省略号截断

    窄列（金额/序号）宽度贴合内容；超长文本列不超过 max_width；末列拉伸填满。
    数据渲染完成后调用。
    """
    table.resizeColumnsToContents()
    for c in range(table.columnCount()):
        w = max(min_width, min(table.columnWidth(c), max_width))
        table.setColumnWidth(c, w)
    table.horizontalHeader().setStretchLastSection(True)


class BaseTableView(QWidget):
    """基础表格页：标题 + 筛选栏 + Fluent 表格 + 导出"""

    def __init__(self, title: str, columns: List[str], hint: str = "", page_key: str | None = None) -> None:
        super().__init__()
        self.columns = columns
        self._col_page_key = page_key or title
        self._meta: dict = {}
        self._filter = TableFilter(reapply_cb=lambda: self.refresh())
        self._sort_col: int | None = None
        self._sort_asc: bool = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        # CaptionLabel 仍用于底部汇总行（self.lbl_summary），故一并保留导入
        from app.ui.widgets import CaptionLabel, PageHeader
        lay.addWidget(PageHeader(title, hint))

        # 筛选栏
        self.filters = QHBoxLayout()
        self.filters.setSpacing(8)
        lay.addLayout(self.filters)
        self._build_filters()

        # Fluent 表格
        self.table = TableWidget(self)
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        # 统一单元格绘制委托（选中保色/悬停全文/冻结灰底）；幂等，重复安装无副作用。
        install_common_features(self.table)
        # 列布局管理器（显示/冻结/顺序/宽度 四态 + 严格填充），取代旧 setStretchLastSection
        self._mgr = ColumnLayoutManager(self._col_page_key, "main")
        self._col_content_w = None
        hdr = self.table.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.sectionMoved.connect(self._on_section_moved)
        hdr.sectionResized.connect(self._on_section_resized)
        lay.addWidget(self.table, 1)

        # 底部：左为汇总（随筛选/搜索变化），中为列设置，右为导出按钮
        bottom = QHBoxLayout()
        self.lbl_summary = CaptionLabel("")
        self.btn_col_settings = PushButton("列设置")
        self.btn_col_settings.clicked.connect(self._open_col_settings)
        self.btn_export = PrimaryPushButton("导出 Excel")
        self.btn_export.clicked.connect(self.export_excel)
        bottom.addWidget(self.lbl_summary)
        bottom.addStretch()
        self.btn_clear_filter = PushButton("清除筛选")
        self.btn_clear_filter.clicked.connect(self._reset_col_filters)
        bottom.addWidget(self.btn_clear_filter)
        bottom.addWidget(self.btn_col_settings)
        bottom.addWidget(self.btn_export)
        lay.addLayout(bottom)

        self._rows: List[List] = []
        # 构造时主动加载一次数据（子类已重写 load_data）
        self.refresh()

    # ---- 筛选栏（子类覆写）----
    def _build_filters(self) -> None:
        raise NotImplementedError

    # ---- 数据（子类覆写）----
    def load_data(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        self.load_data()
        if getattr(self, "_sort_col", None) is not None:
            self._sort_rows()
        self._raw_rows = list(self._rows)
        self._apply_col_filters()
        self._render()
        self._update_filter_marks()

    # ---- 表头：右键排序 + 右键筛选（不再点击表头排序） ----
    def _enable_col_filter(self) -> None:
        """兼容旧名：等同于 _enable_sort_and_filter。"""
        self._enable_sort_and_filter()

    def _enable_sort_and_filter(self) -> None:
        """表头右键菜单：升序/降序排序 + 按列筛选。改「点击表头排序」为「右键排序」。"""
        self._sort_col = None
        self._sort_asc = True
        hdr = self.table.horizontalHeader()
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._on_header_context_menu)

    def _on_header_context_menu(self, pos) -> None:
        menu = QMenu(self.table)
        add_sort_actions(menu, self.table, pos, self._sort_by)
        menu.addSeparator()
        populate_filter_menu(menu, self._filter, self.table, pos)
        menu.exec(self.table.mapToGlobal(pos))

    def _sort_by(self, logical: int, asc: bool) -> None:
        self._sort_col = logical
        self._sort_asc = asc
        self._sort_rows()
        self._apply_col_filters()
        self._render()
        self._update_filter_marks()

    def _on_header_filter(self, pos) -> None:
        open_filter_submenu(self._filter, self.table, pos)

    def _sort_rows(self) -> None:
        """按当前排序列对 self._rows 排序，并同步重排 self._meta（保持索引对齐）。"""
        col = self._sort_col
        if col is None:
            return
        pairs = list(zip(self._rows, [self._meta.get(i) for i in range(len(self._rows))]))

        def keyf(p):
            v = p[0][col]
            if isinstance(v, (int, float)):
                return (0, v)
            return (1, str(v))
        pairs.sort(key=keyf, reverse=not self._sort_asc)
        self._rows = [p[0] for p in pairs]
        self._meta = {i: p[1] for i, p in enumerate(pairs)}

    def _apply_col_filters(self) -> None:
        """用统一筛选引擎过滤行数据（多列 + 搜索，AND 组合），并保持 _meta 索引对齐。"""
        if not self._filter.has_any():
            return
        s = self._filter._search.lower()
        col_filters = self._filter._col_filters
        new_rows = []
        new_meta = {}
        j = 0
        for i, row in enumerate(self._rows):
            keep = True
            if s:
                hit = False
                for v in row:
                    if s in _cell_key(v).lower():
                        hit = True
                        break
                if not hit:
                    keep = False
            if keep:
                for col, vals in col_filters.items():
                    if _cell_key(row[col]) not in vals:
                        keep = False
                        break
            if keep:
                new_rows.append(row)
                new_meta[j] = self._meta.get(i)
                j += 1
        self._rows = new_rows
        self._meta = new_meta

    def _update_filter_marks(self) -> None:
        """已设筛选的列在表头显示漏斗图标（不改动标题文本，避免污染列布局 key）；
        排序列改用自绘「贴角实心直角三角」标记（AccentHeaderView 渲染），不再用 ▲/▼ 文本后缀。

        筛选键与排序列均为逻辑列号；表头项须按视觉列号取，故用 logicalIndex 转换，
        否则列被重排/隐藏后漏斗图标与排序三角会落在错误的列上。
        """
        marks = set(self._filter.active_cols())
        hdr = self.table.horizontalHeader()
        sort_col = getattr(self, "_sort_col", None)
        sort_asc = getattr(self, "_sort_asc", True)
        icon = _funnel_icon()
        for c in range(self.table.columnCount()):
            it = self.table.horizontalHeaderItem(c)
            if it is None:
                continue
            logical = hdr.logicalIndex(c)
            it.setIcon(icon if logical in marks else QIcon())
            # 标题只保留原始文案（不再挂 ▲/▼ 后缀，避免污染列布局 key）
            base = it.text().replace(" ▲", "").replace(" ▼", "")
            if base != it.text():
                it.setText(base)
        # 排序标记交给 AccentHeaderView 自绘直角三角（isSortIndicatorShown=False 时绘制）；
        # 此处仅设置排序指示器的逻辑列/方向，真正的行排序由 _sort_rows 完成。
        if isinstance(sort_col, int) and sort_col >= 0:
            hdr.setSortIndicator(sort_col, Qt.AscendingOrder if sort_asc else Qt.DescendingOrder)
        else:
            hdr.setSortIndicator(-1, Qt.AscendingOrder)

    def _reset_col_filters(self) -> None:
        self._filter.clear_all()  # 内部触发 refresh

    # ---- 列布局：顺序/宽度 持久化 + 列设置 ----
    def _on_section_moved(self, _logical: int, _old: int, _new: int) -> None:
        """用户拖拽表头重排后，保存新的视觉顺序。"""
        hdr = self.table.horizontalHeader()
        order = [self.columns[hdr.logicalIndex(v)] for v in range(self.table.columnCount())]
        state = self._mgr.load(self.columns)
        state["order"] = order
        self._mgr.save(state)

    def _on_section_resized(self, logical: int, _old: int, new_w: int) -> None:
        """用户手动调列宽：记为 fixed 宽度，并立刻重排填充（其余列吸收，保持总宽=视口）。
        程序化重填触发的 resize 由 _applying 屏蔽，避免把其它列误标为 fixed。"""
        if getattr(self, "_applying", False):
            return
        if new_w <= 0 or self._col_content_w is None:
            return
        state = self._mgr.load(self.columns)
        state["widths"][self.columns[logical]] = int(new_w)
        self._mgr.save(state)
        self._applying = True
        try:
            self._mgr.apply(self.table, self.columns, self._col_content_w, sorted_col=self._sort_col)
        finally:
            self._applying = False
        # 布局变化（拖宽/双击自适应）后重绘漏斗图标与排序箭头，避免标记丢失
        self._update_filter_marks()

    def _open_col_settings(self) -> None:
        from app.ui.column_settings_dialog import open_column_settings
        if self._col_content_w is None:
            self._col_content_w = self._mgr.measure_content_widths(self.table, self.columns)
        state = self._mgr.load(self.columns)
        hdr = self.table.horizontalHeader()
        state["order"] = [self.columns[hdr.logicalIndex(v)] for v in range(self.table.columnCount())]
        if open_column_settings(self, self._mgr, self.columns, state, self._col_content_w):
            # 应用后重新测量（显隐/顺序/宽度变了），再填充
            self._col_content_w = self._mgr.measure_content_widths(self.table, self.columns)
            self._mgr.apply(self.table, self.columns, self._col_content_w, sorted_col=self._sort_col)

    def showEvent(self, event) -> None:  # noqa: N802
        """导航切换显示时自动刷新（保证数据最新）"""
        super().showEvent(event)
        self.refresh()

    def resizeEvent(self, event) -> None:  # noqa: N802
        """窗体尺寸变化（含 DPI/分辨率切换）时重算填充，保证始终填满且优先未显全列。"""
        super().resizeEvent(event)
        if getattr(self, "_col_content_w", None) is not None and self.table.columnCount():
            self._mgr.apply(self.table, self.columns, self._col_content_w, sorted_col=self._sort_col)
            # 重绘漏斗图标与排序箭头，避免缩放后标记丢失
            self._update_filter_marks()

    def _render(self) -> None:
        total_cols = self._total_cols()
        # 合计行存在时禁用排序，保证合计行固定底部
        self.table.setSortingEnabled(False)
        # 先清空所有旧行（收缩时保留行内容不被清，必须先 setRowCount(0)）
        self.table.setRowCount(0)
        self.table.setRowCount(len(self._rows) + (1 if total_cols else 0))
        for r, row in enumerate(self._rows):
            for c, val in enumerate(row):
                item = self._make_item(val, r, c)
                self.table.setItem(r, c, item)
            self.table.setRowHeight(r, scale.px(34))
        # 合计行
        if total_cols:
            tr = len(self._rows)
            from PySide6.QtGui import QColor, QFont
            from PySide6.QtWidgets import QTableWidgetItem
            total_item = QTableWidgetItem("合计")
            total_item.setFont(QFont(total_item.font().family(), total_item.font().pointSize(), QFont.Weight.Bold))
            total_item.setBackground(QColor("#F2F2F0"))
            self.table.setItem(tr, 0, total_item)
            for c in total_cols:
                t = sum(row[c] for row in self._rows if isinstance(row[c], (int, float)))
                item = QTableWidgetItem(f"{t:,.2f}")
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                item.setFont(QFont(item.font().family(), item.font().pointSize(), QFont.Weight.Bold))
                item.setBackground(QColor("#F2F2F0"))
                self.table.setItem(tr, c, item)
            self.table.setRowHeight(tr, scale.px(34))
        # 列布局：测量内容宽（封顶发票号宽）→ 应用（重排/显隐/定宽/严格填充）
        self._col_content_w = self._mgr.measure_content_widths(self.table, self.columns)
        self._mgr.apply(self.table, self.columns, self._col_content_w, sorted_col=self._sort_col)
        self.lbl_summary.setText(self._summary_text())

    def _summary_text(self) -> str:
        """底部汇总文案：行数 + 各合计列求和（已随筛选/搜索变化）。

        子类无需覆写；如需自定义可重写本方法。
        """
        parts = [f"共 {len(self._rows)} 行"]
        for c in sorted(self._total_cols()):
            # 过滤本列为数值的行再求和（文本列不会进 _total_cols）
            s = sum(row[c] for row in self._rows if isinstance(row[c], (int, float)))
            parts.append(f"{self.columns[c]} {s:,.2f}")
        return " ｜ ".join(parts)

    def _total_cols(self) -> set:
        """需要合计的列索引（子类覆写）"""
        return set()

    def _make_item(self, val, r: int, c: int):
        from PySide6.QtWidgets import QTableWidgetItem
        item = QTableWidgetItem(self._fmt(val))
        meta = self._meta.get(r)
        row_red = bool(meta and (meta.get("is_red") or meta.get("is_voided")))
        if isinstance(val, (int, float)):
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if val < 0:
                item.setForeground(RED)
            elif val > 0 and c in self._green_cols():
                item.setForeground(GREEN)
        if row_red:
            # 正数发票被红冲 / 红字发票：整行字体标红（委托保证选中仍红）
            item.setForeground(RED)
        item.setData(Qt.ItemDataRole.UserRole, meta)
        return item

    def _green_cols(self) -> set:
        return set()

    @staticmethod
    def _fmt(v) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:,.2f}"
        return str(v)

    def _meta_at(self, row: int):
        """取某行的元数据（排序后仍正确）"""
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ---- 导出 ----
    def export_excel(self) -> None:
        if not self._rows:
            QMessageBox.information(self, "提示", "当前没有数据可导出")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 Excel", "", "Excel 文件 (*.xlsx)")
        if not path:
            return
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(self.columns)
        for row in self._rows:
            ws.append([None if v is None else v for v in row])
        try:
            wb.save(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出完成", f"已导出 {len(self._rows)} 行到:\n{path}")


def make_filter_widgets(parent: QWidget, filters: QHBoxLayout,
                        on_change: Callable, search_label: str = "购方",
                        engine=None) -> Tuple[ComboBox, ComboBox, ComboBox, QLineEdit, None]:
    """年份 + 月份 组合筛选 + 来源 + 搜索 筛选控件

    年份下拉：全部年份 + 各年份；月份下拉：全部月份 + 1-12 月（固定，不随年份联动）。
    年份/月份独立选择，组合成 YYYY / YYYY-MM / MM 三种 period 传给查询（见 build_period）。
    engine 不为 None 时，搜索框并入统一筛选引擎（跨列模糊 + 与列筛选叠加），搜索不再走 DB 重载。
    返回 (year, month, src, search, None)。
    """
    from app.ui.widgets import CaptionLabel

    filters.addWidget(CaptionLabel("开票年份"))
    year = ComboBox()
    year.addItem("全部年份", userData="")
    for y in _year_options():
        year.addItem(y, userData=y)
    year.currentIndexChanged.connect(on_change)
    filters.addWidget(year)

    filters.addWidget(CaptionLabel("开票月份"))
    month = ComboBox()
    month.addItem("全部月份", userData="")
    for label, data in month_options_1_12():
        month.addItem(label, userData=data)
    month.currentIndexChanged.connect(on_change)
    filters.addWidget(month)

    filters.addWidget(CaptionLabel("来源"))
    src = ComboBox()
    src.addItem("全部", userData="")
    src.addItem("台账导入", userData="import")
    src.addItem("手动补录", userData="manual")
    src.currentIndexChanged.connect(on_change)

    filters.addWidget(CaptionLabel(search_label))
    search = LineEdit()
    search.setPlaceholderText(search_label)
    search.setFixedWidth(220)
    if engine is not None:
        # 搜索并入统一引擎：跨列模糊匹配，与列筛选叠加（AND）
        engine.bind_search_widget(search)
        search.textChanged.connect(engine.set_search)
    else:
        search.textChanged.connect(on_change)

    filters.addWidget(year)
    filters.addWidget(month)
    filters.addWidget(src)
    filters.addWidget(search)
    filters.addStretch()
    return year, month, src, search, None


def month_options_1_12() -> List[Tuple[str, str]]:
    """月份下拉固定选项：(展示 "1月".."12月", 数据 "01".."12")，与年份独立。"""
    return [(f"{m}月", f"{m:02d}") for m in range(1, 13)]


def build_period(year_data, month_data) -> str | None:
    """年份 + 月份 组合成 period 字符串：

    - 年+月都选 → "YYYY-MM"（精确月）
    - 仅选年   → "YYYY"（全年）
    - 仅选月   → "MM"（跨年匹配该月）
    - 都不选   → None
    """
    y = year_data or ""
    m = month_data or ""
    if y and m:
        return f"{y}-{m}"
    if y:
        return y
    if m:
        return m
    return None


def _year_options() -> List[str]:
    from app.db import get_conn
    conn = get_conn()
    try:
        return [r["y"] for r in conn.execute(
            "SELECT DISTINCT strftime('%Y', invoice_date) AS y FROM invoice "
            "WHERE invoice_date IS NOT NULL AND invoice_date != '' "
            "AND strftime('%Y', invoice_date) IS NOT NULL ORDER BY y")]
    finally:
        conn.close()
