"""销项发票页（只读）：展示销项导入文档的全部原始行（raw_invoice）

- 数据源：销项导入文档（raw_invoice，仅 import_batch_id 非空的导入行）。
- 只读：无增删改、无同步、无修改记录面板。
- 支持：搜索（发票号/购方/备注）、点击表头排序、按来源 sheet 筛选、红字行浅红标注。
- 排序：金额列（价税合计/不含税/税率/税额）按数值、行号按整数，其余按文本（手动实现，
  因 PySide6 的 QTableWidgetItem 不暴露 setSortRole，无法直接按 UserRole 排序）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.ui.widgets import CaptionLabel, PushButton, SubtitleLabel
from app.ui.column_state import attach_persistence, auto_fit_then_restore
from app.engine import raw_invoice as ri

_HEADERS = ["来源sheet", "行号", "序号", "发票号码", "种类", "开票日期", "状态", "凭证号",
            "购方名称", "价税合计", "不含税", "税率", "税额", "货物或劳务", "备注"]
# 列 -> raw_invoice 字段
_KEYS = ["sheet_name", "row_no", "seq", "invoice_no", "kind", "invoice_date_raw",
         "status", "voucher_no", "buyer", "total_amount_raw", "net_amount_raw",
         "tax_rate_raw", "tax_raw", "goods", "remark"]
_AMOUNT_COLS = (9, 10, 11, 12)  # 价税合计/不含税/税率/税额 按数值排序


def _parse_total(txt) -> float:
    if not txt:
        return 0.0
    try:
        return float(str(txt).replace(",", ""))
    except ValueError:
        return 0.0


def _sort_key(row: dict, col: int):
    if col in _AMOUNT_COLS:
        return _parse_total(row.get(_KEYS[col]) or "")
    if col == 1:  # 行号按整数
        try:
            return int(row.get("row_no") or 0)
        except (ValueError, TypeError):
            return 0
    return (row.get(_KEYS[col]) or "")


class InvoiceLedgerView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        t = SubtitleLabel("销项发票")
        lay.addWidget(t)
        h = CaptionLabel("查看销项导入文档的全部原始信息（逐行 1:1 镜像），只读。"
                         "红字发票整行浅红标注。支持搜索、点击表头排序、按来源 sheet 筛选。")
        lay.addWidget(h)

        # ---- 筛选条 ----
        fbar = QHBoxLayout()
        fbar.addWidget(CaptionLabel("来源sheet"))
        self.f_sheet = QComboBox()
        self.f_sheet.addItem("全部", userData="")
        self.f_sheet.currentIndexChanged.connect(self.refresh)
        fbar.addWidget(self.f_sheet)
        fbar.addWidget(CaptionLabel("状态"))
        self.f_status = QComboBox()
        for label, val in [("全部", ""), ("仅红字", "red")]:
            self.f_status.addItem(label, userData=val)
        self.f_status.currentIndexChanged.connect(self.refresh)
        fbar.addWidget(self.f_status)
        fbar.addWidget(CaptionLabel("搜索"))
        self.f_search = QLineEdit()
        self.f_search.setPlaceholderText("发票号码 / 购方 / 备注")
        self.f_search.textChanged.connect(self.refresh)
        fbar.addWidget(self.f_search, 1)
        self.btn_refresh = PushButton("刷新")
        self.btn_refresh.clicked.connect(self.refresh)
        fbar.addWidget(self.btn_refresh)
        lay.addLayout(fbar)

        # ---- 主表 ----
        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(False)  # 排序由下方手动实现
        self.table.horizontalHeader().sectionClicked.connect(self._on_header)
        attach_persistence(self.table, "invoice_ledger", "main")
        lay.addWidget(self.table, 1)

        self.lbl_stat = CaptionLabel("")
        lay.addWidget(self.lbl_stat)

        self._rows: list[dict] = []
        self._sort_col: int = -1
        self._sort_desc: bool = False
        self.refresh()

    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def _refresh_sheet_combo(self) -> None:
        cur = self.f_sheet.currentData()
        self.f_sheet.blockSignals(True)
        self.f_sheet.clear()
        self.f_sheet.addItem("全部", userData="")
        for s in ri.distinct_sheets(imported_only=True):
            self.f_sheet.addItem(s, userData=s)
        idx = self.f_sheet.findData(cur)
        if idx >= 0:
            self.f_sheet.setCurrentIndex(idx)
        self.f_sheet.blockSignals(False)

    def refresh(self) -> None:
        self._refresh_sheet_combo()
        sheet = self.f_sheet.currentData() or ""
        status = self.f_status.currentData() or ""
        keyword = self.f_search.text().strip()
        self._rows = ri.list_raw(sheet=sheet, status=status, keyword=keyword, imported_only=True)
        self._apply()

    def _apply(self) -> None:
        rows = self._rows
        if self._sort_col >= 0:
            rows = sorted(rows, key=lambda r: _sort_key(r, self._sort_col), reverse=self._sort_desc)
        self._fill(rows)
        hdr = self.table.horizontalHeader()
        if self._sort_col >= 0:
            hdr.setSortIndicator(self._sort_col,
                                 Qt.SortOrder.DescendingOrder if self._sort_desc else Qt.SortOrder.AscendingOrder)
        else:
            hdr.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.lbl_stat.setText(f"共 {len(self._rows)} 行（数据来源：销项导入文档）")

    def _on_header(self, col: int) -> None:
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = False
        self._apply()

    def _fill(self, rows) -> None:
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            total = _parse_total(row.get("total_amount_raw"))
            is_red = total < 0
            vals = [
                row.get("sheet_name") or "",
                str(row.get("row_no") or ""),
                row.get("seq") or "",
                row.get("invoice_no") or "",
                row.get("kind") or "",
                row.get("invoice_date_raw") or "",
                row.get("status") or "",
                row.get("voucher_no") or "",
                row.get("buyer") or "",
                row.get("total_amount_raw") or "",
                row.get("net_amount_raw") or "",
                row.get("tax_rate_raw") or "",
                row.get("tax_raw") or "",
                row.get("goods") or "",
                row.get("remark") or "",
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c in _AMOUNT_COLS:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if is_red and c in (3, 9):  # 红字发票号与金额标红
                    item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(r, c, item)
            if is_red:
                for c in range(self.table.columnCount()):
                    it = self.table.item(r, c)
                    if it is not None:
                        it.setBackground(QColor("#FFECEC"))
        used = auto_fit_then_restore(self.table, "invoice_ledger", "main")
        if used:
            self.table.horizontalHeader().setStretchLastSection(False)
