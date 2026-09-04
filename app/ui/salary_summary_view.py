"""工资累计页（方案 A：独立页面，挂在「工资个税」大类下）

- 数据源：raw_salary 原始镜像 + import_batch 账期，经 engine/salary_summary.py 聚合。
- 按「姓名 + 类别」跨月累计，与「工资表」还原页解耦：还原页逐行复刻源文件，本页只做汇总。
- 支持：年份 / 类别 / 姓名筛选、排序、底部合计；双击（或右键）某人可下钻看其逐月明细。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.engine import salary_summary as ss
from app.engine.raw_salary import SHEET_LABELS, SHEET_ORDER, sheet_label
from app.ui.column_layout import install_column_layout
from app.ui.table_features import (
    install_accent_header, install_common_features, install_header_filter,
)
from app.ui.widgets import CaptionLabel, PageHeader

_HEADERS = ["姓名", "类别", "月数", "每月工资累计", "代扣个所税", "代扣公积金", "实发金额"]


def _money(v) -> str:
    try:
        return f"{float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return ""


_GETTERS = [
    lambda r: r.get("staff_name") or "",
    lambda r: sheet_label(r.get("sheet_key")),
    lambda r: str(r.get("months") or 0),
    lambda r: _money(r.get("gross")),
    lambda r: _money(r.get("tax")),
    lambda r: _money(r.get("fund")),
    lambda r: _money(r.get("net")),
]

# 金额列（右对齐 + 按数值排序）；月数按整数排序
_AMOUNT_COLS = {3, 4, 5, 6}
_MONTH_COL = 2


def _parse_num(txt) -> float:
    from app.engine.raw_salary import _parse_num as _pn
    return _pn(txt)


class SalarySummaryView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        lay.addWidget(PageHeader(
            "工资累计",
            "按月导入的工资数据在此跨月累计（按「姓名 + 类别」汇总）；"
            "月数按去重后的月份统计。双击某一行可查看该人的逐月明细。"
            "本页只读汇总，原始数据仍以「台账查看 → 工资表」为准。",
        ))

        # 筛选条
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.f_year = QComboBox()
        self.f_year.currentIndexChanged.connect(self._apply)
        self.f_sheet = QComboBox()
        self.f_sheet.addItem("全部类别", "")
        self.f_sheet.currentIndexChanged.connect(self._apply)
        self.f_search = QLineEdit()
        self.f_search.setPlaceholderText("搜索姓名")
        bar.addWidget(QLabel("年份："))
        bar.addWidget(self.f_year, 0)
        bar.addWidget(QLabel("类别："))
        bar.addWidget(self.f_sheet, 0)
        bar.addWidget(QLabel("搜索："))
        bar.addWidget(self.f_search, 1)
        lay.addLayout(bar)

        # 表格
        self.table = QTableWidget(0, len(_HEADERS))
        install_accent_header(self.table)
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        install_common_features(self.table)
        self._filter = install_header_filter(self.table)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context)
        self.table.cellDoubleClicked.connect(self._on_double)
        lay.addWidget(self.table, 1)

        self.lbl_stat = CaptionLabel("")
        lay.addWidget(self.lbl_stat)

        self._col = install_column_layout(self.table, "salary_summary", "main")
        self._col.set_sort_callback(self._do_sort)

        self._rows: list[dict] = []
        self._sort_col = -1
        self._sort_order = Qt.SortOrder.AscendingOrder

        self.f_search.textChanged.connect(self._apply)

    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        self._refresh_year_combo()
        self._refresh_sheet_combo()
        self._apply()

    def _refresh_year_combo(self) -> None:
        cur = self.f_year.currentData()
        self.f_year.blockSignals(True)
        self.f_year.clear()
        self.f_year.addItem("全部年份", userData="")
        for y in ss.year_options():
            self.f_year.addItem(y, userData=y)
        idx = self.f_year.findData(cur)
        self.f_year.setCurrentIndex(idx if idx >= 0 else 0)
        self.f_year.blockSignals(False)

    def _refresh_sheet_combo(self) -> None:
        cur = self.f_sheet.currentData()
        self.f_sheet.blockSignals(True)
        self.f_sheet.clear()
        self.f_sheet.addItem("全部类别", "")
        for key in SHEET_ORDER:
            self.f_sheet.addItem(SHEET_LABELS.get(key, key), key)
        idx = self.f_sheet.findData(cur)
        self.f_sheet.setCurrentIndex(idx if idx >= 0 else 0)
        self.f_sheet.blockSignals(False)

    # ---- 筛选 ----
    def _apply(self) -> None:
        self._rows = ss.summary_rows(
            year=self.f_year.currentData() or "",
            sheet_key=self.f_sheet.currentData() or "",
            keyword=self.f_search.text().strip(),
        )
        rows = self._rows
        if self._sort_col >= 0:
            col = self._sort_col
            rev = self._sort_order == Qt.SortOrder.DescendingOrder
            if col == _MONTH_COL:
                rows.sort(key=lambda r: int(r.get("months") or 0), reverse=rev)
            elif col in _AMOUNT_COLS:
                rows.sort(key=lambda r: float(r.get(
                    ("gross", "tax", "fund", "net")[col - 3]) or 0), reverse=rev)
            else:
                rows.sort(key=lambda r: str(_GETTERS[col](r)), reverse=rev)

        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for c, getter in enumerate(_GETTERS):
                item = QTableWidgetItem(str(getter(row)))
                if c in _AMOUNT_COLS or c == _MONTH_COL:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, c, item)

        self._col.apply()
        self._filter.apply_to_table(self.table)
        if self._sort_col >= 0:
            self.table.horizontalHeader().setSortIndicator(self._sort_col, self._sort_order)

        tg = sum(float(r.get("gross") or 0) for r in rows)
        tn = sum(float(r.get("net") or 0) for r in rows)
        scope = self.f_year.currentData() or "全部年份"
        self.lbl_stat.setText(
            f"共 {len(rows)} 条（姓名×类别），范围：{scope}；"
            f"每月工资累计 {tg:,.2f}，实发合计 {tn:,.2f}")

    # ------------------------------------------------------------------ #
    def _do_sort(self, logical, asc) -> None:
        self._sort_col = logical
        self._sort_order = Qt.SortOrder.AscendingOrder if asc else Qt.SortOrder.DescendingOrder
        self._col.set_sort_col(logical)
        self._apply()

    # ---- 下钻：逐月明细 ----
    def _current_row(self):
        r = self.table.currentRow()
        if r < 0 or r >= len(self._rows):
            return None
        return self._rows[r]

    def _on_double(self, *_args) -> None:
        row = self._current_row()
        if row:
            self._show_detail(row)

    def _on_context(self, pos) -> None:
        if not self._current_row():
            return
        menu = QMenu(self)
        act = menu.addAction("查看逐月明细")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == act:
            self._show_detail(self._current_row())

    def _show_detail(self, row: dict) -> None:
        name = row.get("staff_name") or ""
        sheet_key = row.get("sheet_key") or ""
        year = self.f_year.currentData() or ""
        detail = ss.detail_rows(name, sheet_key=sheet_key, year=year)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{name} · 逐月明细（{sheet_label(sheet_key)}"
                           f"{' / ' + year if year else ''}）")
        dlg.resize(760, 460)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        headers = ["账期", "项目", "每月工资", "代扣个所税", "代扣公积金", "实发金额"]
        table = QTableWidget(len(detail), len(headers))
        install_accent_header(table)
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        for i, d in enumerate(detail):
            vals = [d.get("period") or "", d.get("item_type") or "",
                    _money(d.get("gross")), _money(d.get("tax")),
                    _money(d.get("fund")), _money(d.get("net"))]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if c >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(i, c, item)
        v.addWidget(table, 1)

        tg = sum(float(d.get("gross") or 0) for d in detail)
        tn = sum(float(d.get("net") or 0) for d in detail)
        v.addWidget(CaptionLabel(
            f"共 {len(detail)} 条月度记录；每月工资合计 {tg:,.2f}，实发合计 {tn:,.2f}"))

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(dlg.reject)
        v.addWidget(box)
        dlg.exec()
