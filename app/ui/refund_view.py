"""退款确认：红字发票判定 + 手动确认退款（可多次、可部分）"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.engine.refund import evaluate_red_invoices

STATUS_TEXT = {
    "no_orig": "未关联原票",
    "orig_missing": "原票未导入（需补录）",
    "same_month": "原票当月开具，不退款",
    "orig_uncollected": "原票未收款，不退款",
    "need_refund": "需退款",
}


class RefundView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        t = QLabel("退款")
        t.setObjectName("pageTitle")
        lay.addWidget(t)
        h = QLabel("红字发票退款确认。手动填写退款金额与日期，可多次确认（部分退款）。")
        h.setObjectName("pageHint")
        lay.addWidget(h)

        btns = QHBoxLayout()
        self.btn_add = QPushButton("确认退款")
        self.btn_add.setObjectName("primary")
        self.btn_add.clicked.connect(self.add_refund)
        self.btn_view = QPushButton("查看退款明细")
        self.btn_view.clicked.connect(self.view_refunds)
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_view)
        btns.addStretch()
        lay.addLayout(btns)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["红字发票", "红字金额", "原票号码", "原票日期", "判定", "已退金额", "剩余应退"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table)

        self.refresh()

    def refresh(self) -> None:
        conn = get_conn()
        try:
            refunded = {
                r["red_invoice_no"]: r["total"]
                for r in conn.execute(
                    "SELECT red_invoice_no, SUM(refund_amount) AS total FROM refund GROUP BY red_invoice_no"
                )
            }
        finally:
            conn.close()
        rows = evaluate_red_invoices()
        self.table.setRowCount(len(rows))
        self._meta = {}
        for r, item in enumerate(rows):
            no = item["red_invoice_no"]
            done = refunded.get(no, 0.0)
            refundable = abs(item["red_amount"])
            remain = max(refundable - done, 0.0)
            vals = [no, item["red_amount"], item["orig_invoice_no"] or "—",
                    item["orig_invoice_date"] or "—", STATUS_TEXT.get(item["status"], item["status"]),
                    done, remain]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(
                    "" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v))))
            self._meta[r] = item

    # ---- 确认退款 ----
    def add_refund(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一条红字发票")
            return
        item = self._meta[row]
        no = item["red_invoice_no"]

        dlg = QDialog(self)
        dlg.setWindowTitle(f"确认退款：{no}")
        form = QFormLayout(dlg)
        lbl = QLabel(f"红字金额 {item['red_amount']:,.2f}（应退 {abs(item['red_amount']):,.2f}）"
                     f"｜判定：{STATUS_TEXT.get(item['status'], item['status'])}")
        form.addRow(lbl)

        amt = QDoubleSpinBox()
        amt.setRange(0.01, 99999999)
        amt.setDecimals(2)
        amt.setValue(abs(item["red_amount"]))
        form.addRow("本次退款金额", amt)

        date = QDateEdit()
        date.setCalendarPopup(True)
        date.setDisplayFormat("yyyy-MM")
        date.setDate(date.date().currentDate())
        form.addRow("退款日期(月)", date)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        amount = amt.value()
        ym = date.date().toString("yyyy-MM")

        conn = get_conn()
        try:
            done = conn.execute(
                "SELECT COALESCE(SUM(refund_amount),0) FROM refund WHERE red_invoice_no=?", (no,)
            ).fetchone()[0]
            if done + amount > abs(item["red_amount"]) + 0.01:
                QMessageBox.warning(self, "提示", "累计退款金额超过应退金额")
                return
            conn.execute(
                "INSERT INTO refund (red_invoice_no, orig_invoice_no, refund_amount, refund_date) VALUES (?,?,?,?)",
                (no, item["orig_invoice_no"], amount, ym),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            QMessageBox.critical(self, "失败", str(e))
            return
        finally:
            conn.close()
        self.refresh()

    # ---- 明细 ----
    def view_refunds(self) -> None:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT red_invoice_no, orig_invoice_no, refund_amount, refund_date FROM refund ORDER BY refund_date"
            ).fetchall()
        finally:
            conn.close()
        dlg = QDialog(self)
        dlg.setWindowTitle("退款明细")
        dlg.resize(620, 400)
        lay = QVBoxLayout(dlg)
        t = QTableWidget(len(rows), 4)
        t.setHorizontalHeaderLabels(["红字发票", "原票号码", "退款金额", "退款日期"])
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        for r, row in enumerate(rows):
            vals = [row["red_invoice_no"], row["orig_invoice_no"], row["refund_amount"], row["refund_date"]]
            for c, v in enumerate(vals):
                t.setItem(r, c, QTableWidgetItem("" if v is None else
                                                 (f"{v:,.2f}" if isinstance(v, float) else str(v))))
        lay.addWidget(t)
        dlg.exec()
