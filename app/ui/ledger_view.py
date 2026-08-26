"""台账数据页：费用台账的查看/编辑

- 发票 / 收款 / 员工 Tab 已迁出或取消（2.2 员工与员工管理重复、2.3 发票/收款取消）；
- 费用归类已迁至「维护 → 费用类型维护」（2.1）；
- 「修改记录」已迁为独立导航项（维护组下），不在本页内嵌。
- 费用台账编辑也写入 change_log（expense_ledger），在独立「修改记录」页统一展示。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.ui.widgets import (CaptionLabel, PushButton, SubtitleLabel)
from app.db import get_conn
from app.engine.change_log import log_change
from app.ui.column_state import attach_persistence, auto_fit_then_restore, restore_col_widths


class LedgerView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        t = SubtitleLabel("台账数据")
        lay.addWidget(t)
        h = CaptionLabel("费用台账的查看与编辑（修改记录已移至左侧「维护 → 修改记录」独立页面）。")
        lay.addWidget(h)

        self.tab_expense = self._make_table(["账期", "经办人", "费用类型", "金额", "凭证号", "身份"], [0, 1, 2, 3, 5])
        lay.addWidget(self.tab_expense, 1)

        attach_persistence(self.tab_expense, "ledger", "expense")

        btns = QHBoxLayout()
        self.btn_edit = PushButton("编辑所选")
        self.btn_edit.clicked.connect(self.edit_selected)
        self.btn_refresh = PushButton("刷新")
        self.btn_refresh.clicked.connect(self.refresh)
        btns.addWidget(self.btn_edit)
        btns.addWidget(self.btn_refresh)
        btns.addStretch()
        lay.addLayout(btns)

        self.tab_expense.cellDoubleClicked.connect(lambda *_: self.edit_selected())
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def _make_table(self, cols: list, edit_cols: list, *, readonly: bool = False) -> QTableWidget:
        tb = QTableWidget(0, len(cols))
        tb.setHorizontalHeaderLabels(cols)
        tb.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tb.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tb.verticalHeader().setVisible(False)
        tb.horizontalHeader().setStretchLastSection(True)
        tb._edit_cols = edit_cols
        tb._readonly = readonly
        return tb

    def _fill(self, tb: QTableWidget, rows, meta_key: str) -> None:
        tb.setRowCount(0)
        tb.setRowCount(len(rows))
        setattr(self, f"_meta_{meta_key}", {})
        meta = getattr(self, f"_meta_{meta_key}")
        for r, row in enumerate(rows):
            for c, v in enumerate(row[:-1]):
                item = QTableWidgetItem("" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                if isinstance(v, float):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                tb.setItem(r, c, item)
            meta[r] = row[-1]
        used = auto_fit_then_restore(tb, "ledger", meta_key)
        if used:
            tb.horizontalHeader().setStretchLastSection(False)

    # ---- 加载 ----
    def refresh(self) -> None:
        conn = get_conn()
        try:
            exps = conn.execute(
                "SELECT period, actual_handler, expense_type, expense_amount, ticket_no, person_type, id AS key "
                "FROM expense_ledger ORDER BY period, id"
            ).fetchall()
        finally:
            conn.close()
        exp_display = []
        for r in exps:
            exp_display.append([r["period"], r["actual_handler"], r["expense_type"], r["expense_amount"],
                                r["ticket_no"], r["person_type"] or "未标", r["key"]])
        self._fill(self.tab_expense, exp_display, "expense")
        # 修改记录由 AuditView 自行加载（统一审计中心），无需在此填充

    # ---- 编辑（费用；编辑同时写 change_log，修改记录统一展示）----
    def edit_selected(self) -> None:
        if self.tabs.currentWidget() is not self.tab_expense:
            QMessageBox.information(self, "提示", "请先切到「费用」页选择一行再编辑")
            return
        row = self.tab_expense.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一行（或双击）")
            return
        self._edit_expense(row)

    def _edit_expense(self, row: int) -> None:
        eid = self._meta_expense[row]
        conn = get_conn()
        try:
            e = conn.execute("SELECT * FROM expense_ledger WHERE id=?", (eid,)).fetchone()
        finally:
            conn.close()
        if e is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑费用 #{eid}")
        form = QFormLayout(dlg)
        type_edit = QLineEdit(e["expense_type"] or "")
        amt = QDoubleSpinBox(); amt.setRange(-99999999, 99999999); amt.setDecimals(2); amt.setValue(e["expense_amount"] or 0)
        handler_edit = QLineEdit(e["actual_handler"] or "")
        type_combo2 = QComboBox()
        for t in ["合伙", "聘用", "兼职", "未标"]:
            type_combo2.addItem(t, userData=t)
        type_combo2.setCurrentText(e["person_type"] or "未标")
        form.addRow("费用类型", type_edit)
        form.addRow("金额", amt)
        form.addRow("经办人", handler_edit)
        form.addRow("身份", type_combo2)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_type = type_edit.text().strip()
        new_amt = amt.value()
        new_handler = handler_edit.text().strip()
        new_ptype = type_combo2.currentData()
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE expense_ledger SET expense_type=?, expense_amount=?, actual_handler=?, person_type=? WHERE id=?",
                (new_type, new_amt, new_handler, new_ptype, eid))
            # 审计：逐字段记录变更（统一审计中心可见）
            log_change(conn, "expense_ledger", str(eid), "edit", "", "", "费用编辑")
            for f, o, n in (
                ("expense_type", e["expense_type"], new_type),
                ("expense_amount", e["expense_amount"], new_amt),
                ("actual_handler", e["actual_handler"], new_handler),
                ("person_type", e["person_type"], new_ptype),
            ):
                if str(o) != str(n):
                    log_change(conn, "expense_ledger", str(eid), f, o, n, "费用编辑")
            conn.commit()
        except Exception as ex:  # noqa: BLE001
            conn.rollback(); QMessageBox.critical(self, "失败", str(ex)); return
        finally:
            conn.close()
        self.refresh()
