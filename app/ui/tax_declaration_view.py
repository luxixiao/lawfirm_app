"""个税申报数据页（「1-11月个税申报」，挂在「工资个税」大类下）

- 数据：每年 11 月税局导出的「1-11 月累计」申报表，**一年一份**，按年份区分与覆盖。
- 字段按税局**字段编号**映射；税局新增的列会自动出现在表格末尾（附加列），
  减少的列则留空——表格格式年年变也不影响导入与查看。
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

from app.engine import tax_declaration as td
from app.importer.tax_import import guess_year, parse_tax_file
from app.ui.column_layout import install_column_layout
from app.ui.table_features import (
    install_accent_header, install_common_features, install_header_filter,
)
from app.ui.widgets import CaptionLabel, SubtitleLabel

# (表头, 字段, 类型)  t=原文  m=金额
_COLS = [
    ("序号", "seq", "t"),
    ("姓名", "staff_name", "t"),
    ("累计收入额", "income", "m"),
    ("累计减除费用", "basic_deduction", "m"),
    ("累计专项扣除", "special_deduction", "m"),
    ("子女教育", "child_edu", "m"),
    ("赡养老人", "elderly", "m"),
    ("住房贷款利息", "housing_loan", "m"),
    ("住房租金", "housing_rent", "m"),
    ("继续教育", "education", "m"),
    ("3岁以下婴幼儿照护", "infant", "m"),
    ("应纳税所得额", "taxable", "m"),
    ("税率/预扣率", "tax_rate", "t"),
    ("速算扣除数", "quick_ded", "m"),
    ("应纳税额", "payable", "m"),
    ("减免税额", "relief", "m"),
    ("已缴税额", "paid", "m"),
    ("应补/退税额", "refill", "m"),
    ("实际已纳税额", "net_paid", "m"),
]

# 参与底部合计的金额字段
_SUM_FIELDS = ("income", "payable", "paid", "refill", "net_paid")


def _money(v) -> str:
    try:
        return f"{float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return ""


class TaxDeclarationView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        lay.addWidget(SubtitleLabel("1-11月个税申报"))
        lay.addWidget(CaptionLabel(
            "导入每年 11 月税局导出的「1-11 月累计」个税申报数据，一年一份。"
            "字段按税局编号取值，表格新增的列会自动追加在末尾，减少的列留空。"))

        # 操作条：年份 + 导入 + 搜索
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.f_year = QComboBox()
        self.f_year.currentIndexChanged.connect(self.refresh)
        self.btn_import = QPushButton("导入个税申报表")
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

        self._col = install_column_layout(self.table, "tax_declaration", "main")
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
        self._rows = td.list_rows(year=year)
        self._extra = td.extra_columns(year=year)
        self._apply()

    def _refresh_year_combo(self) -> None:
        cur = self.f_year.currentData()
        self.f_year.blockSignals(True)
        self.f_year.clear()
        self.f_year.addItem("全部年份", userData="")
        for y in td.year_options():
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

        if self._sort_col >= 0 and self._sort_col < len(self._headers()):
            col = self._sort_col
            rev = self._sort_order == Qt.SortOrder.DescendingOrder
            base = [c for c in _COLS]
            if col < len(base):
                _h, field, kind = base[col]
                if kind == "m":
                    rows.sort(key=lambda r: float(r.get(field) or 0), reverse=rev)
                elif field == "seq":
                    rows.sort(key=lambda r: _seq_num(r.get("seq")), reverse=rev)
                else:
                    rows.sort(key=lambda r: str(r.get(field) or ""), reverse=rev)
            else:
                name = self._extra[col - len(base)]
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
                is_money = (c < len(_COLS) and _COLS[c][2] == "m")
                if is_money:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, c, item)

        self._col.apply()
        self._filter.apply_to_table(self.table)
        if self._sort_col >= 0:
            self.table.horizontalHeader().setSortIndicator(self._sort_col, self._sort_order)

        year = self.f_year.currentData() or "全部年份"
        self.lbl_stat.setText(
            f"共 {len(rows)} 人（{year}）；累计收入额 {totals['income']:,.2f}｜"
            f"应纳税额 {totals['payable']:,.2f}｜已缴税额 {totals['paid']:,.2f}｜"
            f"应补/退税 {totals['refill']:,.2f}｜实际已纳税额 {totals['net_paid']:,.2f}")

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
            self, "选择个税申报表", "",
            "Excel 文件 (*.xls *.xlsx *.xlsm);;所有文件 (*.*)")
        if not path:
            return
        fname = path.replace("\\", "/").split("/")[-1]
        year = guess_year(fname)
        text, ok = QInputDialog.getText(
            self, "确认申报年份", "申报年份（YYYY）：", text=year)
        if not ok or not text.strip():
            return
        year = text.strip()
        if year in td.year_options():
            ret = QMessageBox.question(
                self, "确认覆盖",
                f"{year} 年已有申报数据（{fname}），重新导入将覆盖旧数据，确定继续？")
            if ret != QMessageBox.StandardButton.Yes:
                return
        try:
            data = parse_tax_file(path)
            if not data["items"]:
                QMessageBox.warning(
                    self, "导入失败",
                    "未能从该文件解析出申报数据行，请确认选择的是税局导出的个税申报表。")
                return
            n = td.save_year(year, data["items"], fname)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(e))
            return
        extra_n = len(data.get("extra_cols") or [])
        tip = f"（其中 {extra_n} 列是模板新增列，已作为附加列显示）" if extra_n else ""
        QMessageBox.information(self, "导入成功", f"{year} 年：共导入 {n} 行。{tip}")
        self.refresh()


def _seq_num(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return 0


def _extra_val(r: dict, name: str) -> str:
    try:
        data = json.loads(r.get("extra_json") or "{}")
    except (TypeError, ValueError):
        return ""
    return str(data.get(name, "") or "")
