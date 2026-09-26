"""费用台账查看页（与「销项发票」「发票台账」同构）

- 数据源：expense_ledger（费用台账导入落库），逐行展示源文件的全部原始列。
- 列：账期/序号/时间/名称/专票号码/经手人/经办人/费用金额/税额/账面费用金额/
      费用类型/凭证号/一级科目/二级科目/身份/来源。
- 能力：账期年月筛选、费用类型筛选、身份筛选、全字段搜索、右键表头排序与按列筛选、
  右击行「编辑此行」（写 change_log）与「查看修改记录」（统一审计中心）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.engine.change_log import log_change
from app.engine.staff_type import person_type_combo_items
from app.ui.audit_view import AuditView
from app.ui.column_layout import install_column_layout
from app.ui.table_features import install_accent_header
from app.ui.table_view import month_options_1_12, build_period
from app.ui.widgets import CaptionLabel, PageHeader

# 显示列（顺序 = 源文件列序，尾部附系统字段）
_HEADERS = ["账期", "序号", "时间", "名称", "专票号码", "经手人", "经办人",
            "费用金额", "税额", "账面费用金额", "费用类型", "凭证号",
            "一级科目", "二级科目", "身份", "来源"]
_KEYS = ["period", "seq", "exp_date", "name", "ticket_no", "handler", "actual_handler",
         "expense_amount", "tax_amount", "book_amount", "expense_type", "voucher_no",
         "subject1", "subject2", "person_type", "source"]
_MONEY_COLS = (7, 8, 9)              # 费用金额 / 税额 / 账面费用金额

# 可编辑字段：(表头, 列名, 控件类型)
_EDIT_FIELDS = [
    ("名称", "name", "text"),
    ("经手人", "handler", "text"),
    ("经办人", "actual_handler", "text"),
    ("费用金额", "expense_amount", "money"),
    ("税额", "tax_amount", "money"),
    ("账面费用金额", "book_amount", "money"),
    ("费用类型", "expense_type", "text"),
    ("凭证号", "voucher_no", "text"),
    ("一级科目", "subject1", "text"),
    ("二级科目", "subject2", "text"),
]


def _fmt_money(v: float) -> str:
    return f"{v:,.2f}"


def _cell_text(v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return _fmt_money(float(v))
    return str(v)


class ExpenseLedgerView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        lay.addWidget(PageHeader(
            "费用台账",
            "查看费用台账源文件的全部内容（逐行 1:1）。"
            "支持账期/类型筛选、搜索、右键表头排序与按列筛选；右击行可编辑或查看修改记录。",
        ))

        # ---- 筛选条 ----
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.f_year = QComboBox()
        self.f_year.currentIndexChanged.connect(self._apply)
        self.f_month = QComboBox()
        self.f_month.currentIndexChanged.connect(self._apply)

        self.f_type = QComboBox()
        self.f_type.currentIndexChanged.connect(self._apply)

        self.f_ptype = QComboBox()
        self.f_ptype.addItem("全部身份", userData="")
        for label, code in person_type_combo_items():
            self.f_ptype.addItem(label, userData=code)
        self.f_ptype.currentIndexChanged.connect(self._apply)

        self.f_search = QLineEdit()
        self.f_search.setPlaceholderText("搜索：名称 / 经办人 / 经手人 / 凭证号 / 专票号码 / 金额")
        self.f_search.textChanged.connect(self._apply)

        bar.addWidget(CaptionLabel("账期年份"))
        bar.addWidget(self.f_year, 0)
        bar.addWidget(CaptionLabel("账期月份"))
        bar.addWidget(self.f_month, 0)
        bar.addWidget(CaptionLabel("费用类型"))
        bar.addWidget(self.f_type, 0)
        bar.addWidget(CaptionLabel("身份"))
        bar.addWidget(self.f_ptype, 0)
        bar.addWidget(CaptionLabel("搜索"))
        bar.addWidget(self.f_search, 1)
        lay.addLayout(bar)

        # ---- 主表 ----
        self.table = QTableWidget(0, len(_HEADERS))
        install_accent_header(self.table)
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)  # 排序由右键表头手动实现
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context)
        self.table.cellDoubleClicked.connect(lambda *_: self._edit_current())

        from app.ui.table_features import install_common_features, install_header_filter
        install_common_features(self.table)
        self._filter = install_header_filter(self.table)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        lay.addWidget(self.table, 1)

        self._col = install_column_layout(self.table, "expense_ledger", "main")
        self._col.set_sort_callback(self._do_sort)

        # ---- 底部统计 ----
        stat = QHBoxLayout()
        self.lbl_count = CaptionLabel("")
        self.lbl_amt = CaptionLabel("")
        self.lbl_tax = CaptionLabel("")
        self.lbl_book = CaptionLabel("")
        stat.addWidget(self.lbl_count)
        stat.addSpacing(28)
        stat.addWidget(self.lbl_amt)
        stat.addSpacing(28)
        stat.addWidget(self.lbl_tax)
        stat.addSpacing(28)
        stat.addWidget(self.lbl_book)
        stat.addStretch()
        lay.addLayout(stat)

        self._all_rows: list[dict] = []
        self._meta: dict[int, int] = {}      # 显示行 -> expense_ledger.id
        self._sort_col = -1
        self._sort_desc = False
        self.refresh()

    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    # ---- 数据 ----
    def refresh(self) -> None:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM expense_ledger ORDER BY period, seq, id"
            ).fetchall()
        finally:
            conn.close()
        self._all_rows = [dict(r) for r in rows]
        self._refresh_year_combo()
        self._refresh_month_combo()
        self._refresh_type_combo()
        self._apply()

    def _years(self) -> list[str]:
        return sorted({(r.get("period") or "")[:4] for r in self._all_rows
                       if (r.get("period") or "")[:4].isdigit()})

    def _month_of(self, r: dict) -> str:
        p = r.get("period") or ""
        return p[5:7] if len(p) >= 7 else ""

    def _refresh_year_combo(self) -> None:
        cur = self.f_year.currentData()
        self.f_year.blockSignals(True)
        self.f_year.clear()
        self.f_year.addItem("全部年份", userData="")
        for y in self._years():
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

    def _refresh_type_combo(self) -> None:
        cur = self.f_type.currentData()
        self.f_type.blockSignals(True)
        self.f_type.clear()
        self.f_type.addItem("全部类型", userData="")
        for t in sorted({(r.get("expense_type") or "").strip() for r in self._all_rows
                         if (r.get("expense_type") or "").strip()}):
            self.f_type.addItem(t, userData=t)
        idx = self.f_type.findData(cur)
        self.f_type.setCurrentIndex(idx if idx >= 0 else 0)
        self.f_type.blockSignals(False)

    def _match_kw(self, row: dict, kw: str) -> bool:
        fields = ("name", "actual_handler", "handler", "voucher_no",
                  "ticket_no", "expense_amount", "subject1", "subject2")
        return any(kw in str(row.get(f) or "").lower() for f in fields)

    def _sort_key(self, row: dict):
        col = self._sort_col
        if col < 0 or col >= len(_KEYS):
            return ""
        v = row.get(_KEYS[col])
        if col in _MONEY_COLS:
            return float(v or 0)
        return str(v or "")

    def _apply(self) -> None:
        rows = self._all_rows

        period = build_period(self.f_year.currentData(), self.f_month.currentData())
        if period:
            if len(period) == 7:
                rows = [r for r in rows if (r.get("period") or "") == period]
            elif len(period) == 4:
                rows = [r for r in rows if (r.get("period") or "")[:4] == period]
            else:
                rows = [r for r in rows if self._month_of(r) == period]

        et = self.f_type.currentData()
        if et:
            rows = [r for r in rows if (r.get("expense_type") or "") == et]

        pt = self.f_ptype.currentData()
        if pt:
            rows = [r for r in rows if (r.get("person_type") or "未标") == pt]

        kw = self.f_search.text().strip().lower()
        if kw:
            rows = [r for r in rows if self._match_kw(r, kw)]

        if self._sort_col >= 0:
            rows = sorted(rows, key=self._sort_key, reverse=self._sort_desc)

        self._fill(rows)
        self._filter.apply_to_table(self.table)   # _fill 重建表格后重放按列筛选
        hdr = self.table.horizontalHeader()
        if self._sort_col >= 0:
            hdr.setSortIndicator(self._sort_col,
                                 Qt.SortOrder.DescendingOrder if self._sort_desc
                                 else Qt.SortOrder.AscendingOrder)
        else:
            hdr.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

        s_amt = sum(float(r.get("expense_amount") or 0) for r in rows)
        s_tax = sum(float(r.get("tax_amount") or 0) for r in rows)
        s_book = sum(float(r.get("book_amount") or 0) for r in rows)
        self.lbl_count.setText(f"共 {len(rows)} 行（数据来源：费用台账源文件）")
        self.lbl_amt.setText(f"费用金额合计：{_fmt_money(s_amt)}")
        self.lbl_tax.setText(f"税额合计：{_fmt_money(s_tax)}")
        self.lbl_book.setText(f"账面费用合计：{_fmt_money(s_book)}")

    def _do_sort(self, logical, asc) -> None:
        self._sort_col = logical
        self._sort_desc = not asc
        self._col.set_sort_col(logical)
        self._apply()

    def _fill(self, rows) -> None:
        self.table.setRowCount(len(rows))
        self._meta = {}
        for r, row in enumerate(rows):
            for c, key in enumerate(_KEYS):
                v = row.get(key)
                item = QTableWidgetItem(_cell_text(v))
                if c in _MONEY_COLS:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)
            self._meta[r] = row["id"]
        self._col.apply()

    # ------------------------------------------------------------------ #
    # 右击菜单：编辑 / 修改记录
    # ------------------------------------------------------------------ #
    def _on_context(self, pos) -> None:
        row = self.table.currentRow()
        if row < 0 or row not in self._meta:
            return
        rid = self._meta[row]
        menu = QMenu(self)
        act_edit = menu.addAction("编辑此行")
        act_log = menu.addAction("查看修改记录")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_edit:
            self._edit_row(rid)
        elif chosen == act_log:
            self._show_log(rid)

    def _edit_current(self) -> None:
        row = self.table.currentRow()
        if row in self._meta:
            self._edit_row(self._meta[row])

    def _show_log(self, rid: int) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(f"修改记录 · expense_ledger #{rid}")
        dlg.resize(900, 520)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        v.addWidget(AuditView(dlg, table_name="expense_ledger", record_id=str(rid), embedded=True), 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(dlg.reject)
        v.addWidget(box)
        dlg.exec()

    def _edit_row(self, rid: int) -> None:
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM expense_ledger WHERE id=?", (rid,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return
        row = dict(row)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑费用台账行 #{rid}")
        form = QFormLayout(dlg)
        widgets: dict[str, object] = {}
        for label, col, kind in _EDIT_FIELDS:
            if kind == "money":
                w = QDoubleSpinBox()
                w.setRange(-99999999, 99999999)
                w.setDecimals(2)
                w.setValue(float(row.get(col) or 0))
            else:
                w = QLineEdit(str(row.get(col) or ""))
            form.addRow(label, w)
            widgets[col] = w

        ptype = QComboBox()
        cur_pt = row.get("person_type") or ""
        for label, code in person_type_combo_items():
            ptype.addItem(label, userData=code)
        idx = ptype.findData(cur_pt)
        ptype.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("身份", ptype)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        form.addRow(box)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_vals = {}
        for _label, col, kind in _EDIT_FIELDS:
            w = widgets[col]
            new_vals[col] = round(w.value(), 2) if kind == "money" else w.text().strip()
        new_vals["person_type"] = ptype.currentData()

        changed = [(c, row.get(c), v) for c, v in new_vals.items() if str(row.get(c) or "") != str(v)]
        if not changed:
            return

        conn = get_conn()
        try:
            sets = ", ".join(f"{c}=?" for c in new_vals)
            conn.execute(f"UPDATE expense_ledger SET {sets} WHERE id=?",
                         (*new_vals.values(), rid))
            log_change(conn, "expense_ledger", str(rid), "edit", "", "", "费用台账编辑")
            for c, old, new in changed:
                log_change(conn, "expense_ledger", str(rid), c, old, new, "费用台账编辑")
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
            raise
        finally:
            conn.close()
        self.refresh()
