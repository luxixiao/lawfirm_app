"""销项发票页（只读）：展示销项导入文档的全部原始行（raw_invoice）

- 数据源：销项导入文档（raw_invoice，仅 import_batch_id 非空的导入行）。
- 只读：无增删改、无同步、无修改记录面板。
- 筛选：开票年份 + 开票月份（两级联动，与「发票收款情况」同款）、状态（全部/仅红字）、
  搜索（发票号码 / 购方 / 金额 / 凭证号）。
- 排序：金额列（价税合计/不含税/税率/税额）按数值，其余按文本（手动实现，
  因 PySide6 的 QTableWidgetItem 不暴露 setSortRole，无法直接按 UserRole 排序）。
- 红字发票整行浅红标注，发票号与金额标红。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QLineEdit,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.ui.widgets import CaptionLabel, SubtitleLabel
from app.ui.column_state import attach_persistence, auto_fit_then_restore
from app.engine import raw_invoice as ri

_HEADERS = ["发票号码", "种类", "开票日期", "状态", "凭证号", "购方名称",
            "价税合计", "不含税", "税率", "税额", "货物或劳务", "备注"]
# 列 -> raw_invoice 字段
_KEYS = ["invoice_no", "kind", "invoice_date_raw", "status", "voucher_no", "buyer",
         "total_amount_raw", "net_amount_raw", "tax_rate_raw", "tax_raw", "goods", "remark"]
_AMOUNT_COLS = (6, 7, 8, 9)  # 价税合计/不含税/税率/税额 按数值排序


def _parse_total(txt) -> float:
    if not txt:
        return 0.0
    try:
        return float(str(txt).replace(",", ""))
    except ValueError:
        return 0.0


def _ym(date_raw) -> str:
    """从原始开票日期取 YYYY-MM；无法解析时返回空串。"""
    s = (date_raw or "")[:7]
    return s if len(s) == 7 and s[:4].isdigit() else ""


def _match_kw(row: dict, kw: str) -> bool:
    """搜索：发票号码 / 购方 / 金额 / 凭证号（不区分大小写、子串匹配）。"""
    fields = [row.get("invoice_no"), row.get("buyer"),
              row.get("total_amount_raw"), row.get("voucher_no")]
    return any(kw in str(f or "").lower() for f in fields)


def _fmt_money(v: float) -> str:
    """金额格式化：千分位 + 2 位小数。"""
    return f"{v:,.2f}"


def _sort_key(row: dict, col: int):
    if col in _AMOUNT_COLS:
        return _parse_total(row.get(_KEYS[col]) or "")
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
                         "红字发票整行浅红标注。支持搜索、点击表头排序、按开票年月筛选。")
        lay.addWidget(h)

        # ---- 筛选条 ----
        fbar = QHBoxLayout()
        fbar.addWidget(CaptionLabel("开票年份"))
        self.f_year = QComboBox()
        self.f_year.currentIndexChanged.connect(self._on_year_changed)
        fbar.addWidget(self.f_year)

        fbar.addWidget(CaptionLabel("开票月份"))
        self.f_month = QComboBox()
        self.f_month.currentIndexChanged.connect(self._apply)
        fbar.addWidget(self.f_month)

        fbar.addWidget(CaptionLabel("状态"))
        self.f_status = QComboBox()
        for label, val in [("全部", ""), ("仅红字", "red")]:
            self.f_status.addItem(label, userData=val)
        self.f_status.currentIndexChanged.connect(self._apply)
        fbar.addWidget(self.f_status)

        fbar.addWidget(CaptionLabel("搜索"))
        self.f_search = QLineEdit()
        self.f_search.setPlaceholderText("发票号码 / 购方 / 金额 / 凭证号")
        self.f_search.textChanged.connect(self._apply)
        fbar.addWidget(self.f_search, 1)

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

        # ---- 底部状态栏：行数 + 三类合计（随筛选动态变化） ----
        fbar2 = QHBoxLayout()
        self.lbl_count = CaptionLabel("")
        self.lbl_amt = CaptionLabel("")
        self.lbl_tax = CaptionLabel("")
        self.lbl_total = CaptionLabel("")
        fbar2.addWidget(self.lbl_count)
        fbar2.addSpacing(28)
        fbar2.addWidget(self.lbl_amt)
        fbar2.addSpacing(28)
        fbar2.addWidget(self.lbl_tax)
        fbar2.addSpacing(28)
        fbar2.addWidget(self.lbl_total)
        fbar2.addStretch()
        lay.addLayout(fbar2)

        self._all_rows: list[dict] = []
        self._sort_col: int = -1
        self._sort_desc: bool = False
        self.refresh()

    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def _year_options(self) -> list[str]:
        return sorted({_ym(r.get("invoice_date_raw"))[:4]
                       for r in self._all_rows if _ym(r.get("invoice_date_raw"))})

    def _month_options(self, year: str = "") -> list[str]:
        return sorted({ym for r in self._all_rows
                       if (ym := _ym(r.get("invoice_date_raw"))) and ym.startswith(year)})

    def _refresh_year_combo(self) -> None:
        cur = self.f_year.currentData()
        self.f_year.blockSignals(True)
        self.f_year.clear()
        self.f_year.addItem("全部年份", userData="")
        for y in self._year_options():
            self.f_year.addItem(y, userData=y)
        idx = self.f_year.findData(cur)
        self.f_year.setCurrentIndex(idx if idx >= 0 else 0)
        self.f_year.blockSignals(False)

    def _refresh_month_combo(self) -> None:
        cur = self.f_month.currentData()
        year = self.f_year.currentData() or ""
        self.f_month.blockSignals(True)
        self.f_month.clear()
        self.f_month.addItem("全部月份", userData="")
        for m in self._month_options(year):
            self.f_month.addItem(m, userData=m)
        idx = self.f_month.findData(cur)
        self.f_month.setCurrentIndex(idx if idx >= 0 else 0)
        self.f_month.blockSignals(False)

    def _on_year_changed(self, *_args) -> None:
        # 年份变化 -> 重建月份下拉（仅保留该年月份），再应用筛选
        self._refresh_month_combo()
        self._apply()

    def refresh(self) -> None:
        self._all_rows = ri.list_raw(imported_only=True)
        self._refresh_year_combo()
        self._refresh_month_combo()
        self._apply()

    def _apply(self) -> None:
        rows = self._all_rows

        if self.f_status.currentData() == "red":
            rows = [r for r in rows if _parse_total(r.get("total_amount_raw")) < 0]

        ym = self.f_month.currentData() or self.f_year.currentData() or ""
        if ym:
            if len(ym) == 7:  # 月份 YYYY-MM
                rows = [r for r in rows if _ym(r.get("invoice_date_raw")) == ym]
            else:             # 年份 YYYY
                rows = [r for r in rows if (r.get("invoice_date_raw") or "")[:4] == ym]

        kw = self.f_search.text().strip().lower()
        if kw:
            rows = [r for r in rows if _match_kw(r, kw)]

        if self._sort_col >= 0:
            rows = sorted(rows, key=lambda r: _sort_key(r, self._sort_col), reverse=self._sort_desc)

        self._fill(rows)
        hdr = self.table.horizontalHeader()
        if self._sort_col >= 0:
            hdr.setSortIndicator(self._sort_col,
                                 Qt.SortOrder.DescendingOrder if self._sort_desc else Qt.SortOrder.AscendingOrder)
        else:
            hdr.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        total_amt = sum(_parse_total(r.get("net_amount_raw")) for r in rows)
        total_tax = sum(_parse_total(r.get("tax_raw")) for r in rows)
        total_sum = sum(_parse_total(r.get("total_amount_raw")) for r in rows)
        self.lbl_count.setText(f"共 {len(rows)} 行（数据来源：销项导入文档）")
        self.lbl_amt.setText(f"合计金额（不含税）：{_fmt_money(total_amt)}")
        self.lbl_tax.setText(f"合计税额：{_fmt_money(total_tax)}")
        self.lbl_total.setText(f"合计总额（价税合计）：{_fmt_money(total_sum)}")

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
                if is_red and c in (0, 6):  # 红字发票号与金额标红（发票号码=0，价税合计=6）
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
