"""费用扣除页（挂在「工资个税」大类下，与「1-11月个税申报」并排）

- 数据：每年税局导出的「1-12 月」个税费用扣除情况，**一年一份**，按年份区分与覆盖。
- 该表没有税局字段编号行，字段按**列名关键词**匹配；税局新增的列会自动出现在表格末尾
  （附加列），减少的列留空——表格格式年年变也不影响导入与查看。
- 页面自带导入按钮：选文件 → 按文件名猜年份 → 可人工确认/修改 → 覆盖式导入。
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.engine import tax_deduction as tded
from app.importer.deduction_import import guess_year, parse_deduction_file
from app.ui.column_layout import install_column_layout
from app.ui.table_features import (
    install_accent_header, install_common_features, install_header_filter,
)
from app.ui.widgets import CaptionLabel, SubtitleLabel

# (表头, 字段, 类型)  t=原文  m=金额
_COLS = [
    ("姓名", "staff_name", "t"),
    ("累计减除费用", "basic_deduction", "m"),
    ("累计专项扣除", "special_deduction", "m"),
    ("累计专项附加扣除", "additional_total", "m"),
    ("子女教育", "child_edu", "m"),
    ("继续教育", "education", "m"),
    ("住房贷款利息", "housing_loan", "m"),
    ("住房租金", "housing_rent", "m"),
    ("赡养老人", "elderly", "m"),
    ("3岁以下婴幼儿照护", "infant", "m"),
    ("个人养老金", "pension", "m"),
    ("其他扣除", "other_deduction", "m"),
    ("准予扣除的捐赠", "donation", "m"),
    ("其他单位累计收入", "other_income", "m"),
    ("其他单位累计扣除", "other_deduct", "m"),
    ("其他单位累计减免税额", "other_relief", "m"),
]

# 参与底部合计的金额字段
_SUM_FIELDS = ("basic_deduction", "special_deduction", "additional_total",
               "other_income", "other_deduct", "other_relief")


def _money(v) -> str:
    try:
        return f"{float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return ""


class TaxDeductionView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        lay.addWidget(SubtitleLabel("费用扣除"))
        lay.addWidget(CaptionLabel(
            "导入每年税局导出的「1-12 月」个税费用扣除数据，一年一份。"
            "字段按列名关键词取值，表格新增的列会自动追加在末尾，减少的列留空。"))

        # 操作条：年份 + 导入 + 搜索
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.f_year = QComboBox()
        self.f_year.currentIndexChanged.connect(self.refresh)
        self.btn_import = QPushButton("导入费用扣除表")
        self.btn_import.setObjectName("primary")
        self.btn_import.clicked.connect(self._on_import)
        self.f_search = QLineEdit()
        self.f_search.setPlaceholderText("搜索姓名")
        bar.addWidget(QLabel("年份："))
        bar.addWidget(self.f_year, 0)
        bar.addWidget(self.btn_import)
        bar.addStretch(1)
        bar.addWidget(QLabel("搜索："))
        bar.addWidget(self.f_search, 1)
        lay.addLayout(bar)

        # 表格
        self.table = QTableWidget(0, len(_COLS))
        install_accent_header(self.table)
        self.table.setHorizontalHeaderLabels([c[0] for c in _COLS])
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
        lay.addWidget(self.table, 1)

        self.lbl_stat = CaptionLabel("")
        lay.addWidget(self.lbl_stat)

        self._col = install_column_layout(self.table, "tax_deduction", "main")
        self._col.set_sort_callback(self._do_sort)

        self._rows: list[dict] = []
        self._extra: list[str] = []
        self._sort_col = -1
        self._sort_order = Qt.SortOrder.AscendingOrder

        self.f_search.textChanged.connect(self._apply)

    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        year = self.f_year.currentData() or ""
        self._refresh_year_combo()
        self._rows = tded.list_rows(year=year)
        self._extra = tded.extra_columns(year=year)
        self._apply()

    def _refresh_year_combo(self) -> None:
        cur = self.f_year.currentData()
        self.f_year.blockSignals(True)
        self.f_year.clear()
        self.f_year.addItem("全部年份", userData="")
        for y in tded.year_options():
            self.f_year.addItem(y, userData=y)
        idx = self.f_year.findData(cur)
        self.f_year.setCurrentIndex(idx if idx >= 0 else 0)
        self.f_year.blockSignals(False)

    # ---- 渲染 ----
    def _headers(self) -> list[str]:
        return [c[0] for c in _COLS] + list(self._extra)

    def _row_values(self, r: dict) -> list[str]:
        vals = []
        for _h, field, kind in _COLS:
            v = r.get(field)
            vals.append(str(v or "") if kind == "t" else _money(v))
        try:
            extra = json.loads(r.get("extra_json") or "{}")
        except (TypeError, ValueError):
            extra = {}
        for name in self._extra:
            vals.append(str(extra.get(name, "") or ""))
        return vals

    def _apply(self) -> None:
        rows = self._rows
        kw = self.f_search.text().strip()
        if kw:
            rows = [r for r in rows if kw in str(r.get("staff_name") or "")]

        if 0 <= self._sort_col < len(self._headers()):
            col = self._sort_col
            rev = self._sort_order == Qt.SortOrder.DescendingOrder
            if col < len(_COLS):
                _h, field, kind = _COLS[col]
                if kind == "m":
                    rows.sort(key=lambda r: float(r.get(field) or 0), reverse=rev)
                else:
                    rows.sort(key=lambda r: str(r.get(field) or ""), reverse=rev)
            else:
                name = self._extra[col - len(_COLS)]
                rows.sort(key=lambda r: _extra_val(r, name), reverse=rev)

        headers = self._headers()
        if self.table.columnCount() != len(headers):
            self.table.setColumnCount(len(headers))
            install_accent_header(self.table)
            self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))

        totals = {f: 0.0 for f in _SUM_FIELDS}
        for i, r in enumerate(rows):
            for f in _SUM_FIELDS:
                try:
                    totals[f] += float(r.get(f) or 0)
                except (TypeError, ValueError):
                    pass
            for c, v in enumerate(self._row_values(r)):
                item = QTableWidgetItem(v)
                if c < len(_COLS) and _COLS[c][2] == "m":
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, c, item)

        self._col.apply()
        self._filter.apply_to_table(self.table)
        if self._sort_col >= 0:
            self.table.horizontalHeader().setSortIndicator(self._sort_col, self._sort_order)

        year = self.f_year.currentData() or "全部年份"
        self.lbl_stat.setText(
            f"共 {len(rows)} 人（{year}）；累计减除费用 {totals['basic_deduction']:,.2f}｜"
            f"累计专项扣除 {totals['special_deduction']:,.2f}｜"
            f"专项附加扣除 {totals['additional_total']:,.2f}｜"
            f"其他单位收入 {totals['other_income']:,.2f}")

    # ------------------------------------------------------------------ #
    def _do_sort(self, logical, asc) -> None:
        self._sort_col = logical
        self._sort_order = Qt.SortOrder.AscendingOrder if asc else Qt.SortOrder.DescendingOrder
        self._col.set_sort_col(logical)
        self._apply()

    # ------------------------------------------------------------------ #
    # 导入
    # ------------------------------------------------------------------ #
    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择费用扣除表", "",
            "Excel 文件 (*.xls *.xlsx *.xlsm);;所有文件 (*.*)")
        if not path:
            return
        fname = path.replace("\\", "/").split("/")[-1]
        year = guess_year(fname)
        text, ok = QInputDialog.getText(self, "确认年份", "年份（YYYY）：", text=year)
        if not ok or not text.strip():
            return
        year = text.strip()
        if year in tded.year_options():
            ret = QMessageBox.question(
                self, "确认覆盖",
                f"{year} 年已有费用扣除数据，重新导入将覆盖旧数据，确定继续？")
            if ret != QMessageBox.StandardButton.Yes:
                return
        try:
            data = parse_deduction_file(path)
            if not data["items"]:
                QMessageBox.warning(
                    self, "导入失败",
                    "未能从该文件解析出数据行，请确认选择的是税局导出的费用扣除表。")
                return
            n = tded.save_year(year, data["items"], fname)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(e))
            return
        extra_n = len(data.get("extra_cols") or [])
        tip = f"（其中 {extra_n} 列为模板新增/重名列，已作为附加列显示）" if extra_n else ""
        QMessageBox.information(self, "导入成功", f"{year} 年：共导入 {n} 行。{tip}")
        self.refresh()


def _extra_val(r: dict, name: str) -> str:
    try:
        data = json.loads(r.get("extra_json") or "{}")
    except (TypeError, ValueError):
        return ""
    return str(data.get(name, "") or "")
