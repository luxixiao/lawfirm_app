"""发票台账查看页（统一审计中心方案）

- 数据源：raw_ledger（发票台账文档逐 sheet 逐行 1:1 镜像，导入双写落库）。
- 只读查看：可按工作表切换、搜索（发票号/对方/金额/案号）、排序、筛选（仅红字）。
- 数据修改统一收归「数据导入 → 导入复核」页（导入前确认 / 导入后回写，全程留痕）：
  本页右击「编辑此行」仅作引导提示；「查看修改记录」打开统一审计中心
  （预设 raw_ledger + 行 id，含 导入修正/(同步) 等事件）。
- 与「销项发票」页同构（raw_invoice vs raw_ledger），概念统一。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.engine import raw_ledger as rl
from app.engine.raw_ledger import sheet_label
from app.ui import style
from app.ui.audit_view import AuditView
from app.ui.column_layout import install_column_layout
from app.ui.table_features import install_accent_header
from app.ui.table_view import month_options_1_12, build_period
from app.ui.widgets import CaptionLabel, PageHeader, PushButton

# 显示列定义：(表头, 取值函数)
def _date_of(r: dict) -> str:
    if r.get("kind") == "prepayment":
        return r.get("recv_date_raw") or ""
    return r.get("invoice_date_raw") or ""

_HEADERS = ["序号", "工作表", "日期", "发票号码", "对方", "金额", "经办人", "备注", "案号"]
_GETTERS = [
    lambda r: r.get("seq") or "",
    lambda r: ((r.get("period") or "") + sheet_label(r.get("sheet_key"))),
    _date_of,
    lambda r: r.get("invoice_no") or "",
    lambda r: r.get("buyer") or "",
    lambda r: r.get("amount_raw") or "",
    lambda r: r.get("handler_text") or "",
    lambda r: r.get("remark") or "",
    lambda r: r.get("case_no") or "",
]
_AMOUNT_COL = 5  # 金额列（按数值排序 + 红字标红）


def _parse_total(txt) -> float:
    from app.engine.raw_ledger import _parse_total as _pt
    return _pt(txt)


class InvoiceLedgerDocView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        lay.addWidget(PageHeader(
            "发票台账",
            "数据来源：发票台账文档（最新一次导入），逐 sheet 逐行镜像；只读查看，可搜索/排序/筛选。"
            "如需修改数据请前往「数据导入 → 导入复核」页（导入前确认 / 导入后回写，全程留痕）；"
            "右击可查看修改记录。",
        ))

        # 筛选条
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.f_year = QComboBox()
        self.f_year.currentIndexChanged.connect(self._on_year_changed)
        self.f_month = QComboBox()
        self.f_month.currentIndexChanged.connect(self._apply)
        self.f_sheet = QComboBox()
        self.f_sheet.addItem("全部工作表", "")
        self.f_status = QComboBox()
        self.f_status.addItem("全部", "")
        self.f_status.addItem("仅红字", "red")
        self.f_search = QLineEdit()
        self.f_search.setPlaceholderText("搜索：发票号码 / 对方 / 金额 / 案号 / 经办人")
        bar.addWidget(QLabel("台账年份："))
        bar.addWidget(self.f_year, 0)
        bar.addWidget(QLabel("台账月份："))
        bar.addWidget(self.f_month, 0)
        bar.addWidget(QLabel("工作表："))
        bar.addWidget(self.f_sheet, 0)
        bar.addWidget(QLabel("状态："))
        bar.addWidget(self.f_status, 0)
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
        from app.ui.table_features import install_common_features, install_header_filter, add_sort_actions
        install_common_features(self.table)
        self._filter = install_header_filter(self.table)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context)
        lay.addWidget(self.table, 1)

        self.lbl_stat = CaptionLabel("")
        lay.addWidget(self.lbl_stat)

        self._col = install_column_layout(self.table, "ledger_doc", "main")
        self._col.set_sort_callback(self._do_sort)

        self._all_rows: list[dict] = []
        self._meta: dict[int, int] = {}      # 显示行 -> raw_ledger.id
        self._sort_col = -1
        self._sort_order = Qt.SortOrder.AscendingOrder

        self.f_sheet.currentIndexChanged.connect(self._apply)
        self.f_status.currentIndexChanged.connect(self._apply)
        # 输入即筛选（与「销项发票」页一致），不依赖回车，内存过滤不重查 DB
        self.f_search.textChanged.connect(self._apply)

    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        # 一次性取全部镜像行，后续筛选（年月/工作表/状态/搜索）均内存过滤，不重查 DB
        self._all_rows = rl.list_raw()
        self._refresh_year_combo()
        self._refresh_month_combo()
        self._refresh_sheet_combo()
        self._apply()

    # ---- 年份 / 月份 下拉（组合选择：年份下拉 + 固定 1-12 月，按导入账期 period 如 "2025-01"）----
    def _year_options(self) -> list:
        return sorted({p[:4] for r in self._all_rows
                       if (p := (r.get("period") or ""))[:4]})

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
        self.f_month.blockSignals(True)
        self.f_month.clear()
        self.f_month.addItem("全部月份", userData="")
        for label, data in month_options_1_12():
            self.f_month.addItem(label, userData=data)
        idx = self.f_month.findData(cur)
        self.f_month.setCurrentIndex(idx if idx >= 0 else 0)
        self.f_month.blockSignals(False)

    def _on_year_changed(self, *_args) -> None:
        # 年份变化 -> 重新应用筛选（月份为固定 1-12，不随年份重建）
        self._apply()

    def _refresh_sheet_combo(self) -> None:
        # 重建工作表下拉（保留当前选择），展示名用干净表类型名
        cur = self.f_sheet.currentData()
        self.f_sheet.blockSignals(True)
        self.f_sheet.clear()
        self.f_sheet.addItem("全部工作表", "")
        for s in rl.sheet_keys():
            self.f_sheet.addItem(s["sheet_name"], s["sheet_key"])
        idx = self.f_sheet.findData(cur)
        if idx >= 0:
            self.f_sheet.setCurrentIndex(idx)
        self.f_sheet.blockSignals(False)

    def _apply(self) -> None:
        rows = self._all_rows

        # 年月筛选（按导入账期 period，如 "2025-01"；组合 year+month 成 YYYY-MM / YYYY / MM）
        period = build_period(self.f_year.currentData(), self.f_month.currentData())
        if period:
            if len(period) == 7:  # YYYY-MM 精确月
                rows = [r for r in rows if (r.get("period") or "") == period]
            elif len(period) == 4:  # 仅年份
                rows = [r for r in rows if (r.get("period") or "")[:4] == period]
            else:  # 仅月份（跨年匹配该月）
                rows = [r for r in rows if (r.get("period") or "")[-2:] == period]

        # 工作表筛选（sheet_key）
        sheet_key = self.f_sheet.currentData() or ""
        if sheet_key:
            rows = [r for r in rows if r.get("sheet_key") == sheet_key]

        # 状态筛选（仅红字）
        if self.f_status.currentData() == "red":
            rows = [r for r in rows if _parse_total(r.get("amount_raw")) < 0]

        # 关键字过滤（输入即筛，内存过滤，不重查 DB）
        kw = self.f_search.text().strip().lower()
        if kw:
            rows = [r for r in rows if any(
                kw in str(r.get(f) or "").lower()
                for f in ("invoice_no", "buyer", "amount_raw", "case_no", "handler_text")
            )]

        # 排序
        if self._sort_col >= 0:
            col = self._sort_col
            rev = self._sort_order == Qt.SortOrder.DescendingOrder
            if col == 0:  # 序号
                rows.sort(key=lambda r: _safe_int(_GETTERS[0](r)), reverse=rev)
            elif col == _AMOUNT_COL:  # 金额
                rows.sort(key=lambda r: _parse_total(_GETTERS[col](r)), reverse=rev)
            else:
                rows.sort(key=lambda r: str(_GETTERS[col](r)), reverse=rev)

        self.table.setRowCount(len(rows))
        self._meta.clear()
        total = 0.0
        for r, row in enumerate(rows):
            self._meta[r] = row["id"]
            is_red = _parse_total(row.get("amount_raw")) < 0
            total += _parse_total(row.get("amount_raw"))
            for c, getter in enumerate(_GETTERS):
                v = getter(row)
                item = QTableWidgetItem("" if v is None else str(v))
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                if c == _AMOUNT_COL:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if is_red:
                    # 负数发票：整行所有字体标红（委托保证选中仍红）
                    item.setForeground(style.qcolor("neg_fg"))
                self.table.setItem(r, c, item)

        self._col.apply()
        # 重新叠加右键「按列筛选」（与搜索/下拉 AND 组合）：_apply 重建表格会清除 setRowHidden 状态
        self._filter.apply_to_table(self.table)
        self.table.horizontalHeader().setSortIndicator(
            self._sort_col, self._sort_order) if self._sort_col >= 0 else None
        self.lbl_stat.setText(
            f"共 {len(rows)} 行（数据来源：发票台账文档）；合计金额 {total:,.2f}"
        )

    # ------------------------------------------------------------------ #
    # 排序
    # ------------------------------------------------------------------ #
    def _do_sort(self, logical, asc) -> None:
        """右键表头排序回调：按逻辑列号排序（支持列重排后保持一致）。"""
        self._sort_col = logical
        self._sort_order = Qt.SortOrder.AscendingOrder if asc else Qt.SortOrder.DescendingOrder
        self._col.set_sort_col(logical)
        self._apply()

    # ------------------------------------------------------------------ #
    # 右击菜单
    # ------------------------------------------------------------------ #
    def _on_context(self, pos) -> None:
        row = self.table.currentRow()
        if row < 0 or row not in self._meta:
            return
        rid = self._meta[row]
        menu = QMenu(self)
        act_edit = menu.addAction("编辑此行（已移至导入复核）")
        act_log = menu.addAction("查看修改记录")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_edit:
            self._guide_to_review()
        elif chosen == act_log:
            self._show_log(rid)

    @staticmethod
    def _guide_to_review() -> None:
        """数据修改统一收归「导入复核」页（阶段 5 起本页只读）。"""
        from qfluentwidgets import InfoBar, InfoBarPosition
        parent = QApplication.activeWindow()
        InfoBar.info(
            "", "台账数据修改请前往「数据导入 → 导入复核」页"
                "（导入前确认 / 导入后回写，全程留痕）",
            parent=parent, position=InfoBarPosition.TOP_RIGHT, duration=4000)

    def _show_log(self, rid: int) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(f"修改记录 · raw_ledger #{rid}")
        dlg.resize(900, 520)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        v.addWidget(AuditView(dlg, table_name="raw_ledger", record_id=str(rid), embedded=True), 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(dlg.reject)
        v.addWidget(box)
        dlg.exec()


def _safe_int(v) -> int:
    try:
        return int(str(v))
    except (ValueError, TypeError):
        return 0
