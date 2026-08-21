"""预收款管理：列表 + 核销（按案号建议，手动确认，部分核销挂账）"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from qfluentwidgets import (SubtitleLabel, CaptionLabel, PrimaryPushButton, PushButton)
from app.db import get_conn


class PrepaymentView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        t = SubtitleLabel("预收款")
        lay.addWidget(t)
        h = QLabel("已入账未开票（sheet4）。选择预收款 → 核销到发票（按案号建议、手动确认，可部分核销）。")
        h.setStyleSheet("color:#8A8886;")
        lay.addWidget(h)

        btns = QHBoxLayout()
        self.btn_offset = QPushButton("核销到发票")
        self.btn_offset.setObjectName("primary")
        self.btn_offset.clicked.connect(self.offset)
        self.btn_view = QPushButton("查看核销明细")
        self.btn_view.clicked.connect(self.view_offsets)
        btns.addWidget(self.btn_offset)
        btns.addWidget(self.btn_view)
        btns.addStretch()
        lay.addLayout(btns)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["收到日期", "对方", "金额", "经办人", "案号", "备注", "已核销", "剩余余额"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table)

        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        """切换到本页时自动刷新数据"""
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT p.*, COALESCE(o.offset_total,0) AS offset_total
                   FROM prepayment p
                   LEFT JOIN (SELECT prepayment_id, SUM(offset_amount) AS offset_total
                              FROM prepayment_offset GROUP BY prepayment_id) o
                   ON o.prepayment_id = p.id
                   ORDER BY p.received_date"""
            ).fetchall()
        finally:
            conn.close()
        self.table.setRowCount(len(rows))
        self._meta = {}
        for r, row in enumerate(rows):
            offset = row["offset_total"] or 0.0
            remain = row["amount"] - offset
            vals = [row["received_date"], row["buyer"], row["amount"], row["person_text"],
                    row["case_no"], row["remark"], offset, remain]
            for c, v in enumerate(vals):
                item = QTableWidgetItem("" if v is None else
                                        (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                self.table.setItem(r, c, item)
            self._meta[r] = {"id": row["id"], "buyer": row["buyer"], "amount": row["amount"],
                             "case_no": row["case_no"], "remain": remain}

    # ---- 核销 ----
    def offset(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一条预收款")
            return
        m = self._meta[row]
        dlg = QDialog(self)
        dlg.setWindowTitle(f"核销预收款：{m['buyer']}")
        dlg.resize(560, 400)
        form = QFormLayout(dlg)
        lbl = QLabel(f"预收款 {m['amount']:,.2f}，剩余 {m['remain']:,.2f}（案号：{m['case_no'] or '无'}）")
        form.addRow(lbl)

        combo = QComboBox()
        conn = get_conn()
        try:
            cands = []
            # 候选：同案号（或全部未核销）的发票
            if m["case_no"]:
                cands = conn.execute(
                    "SELECT invoice_no, invoice_date, buyer, total_amount, case_no FROM invoice "
                    "WHERE case_no LIKE ? AND total_amount > 0 ORDER BY invoice_date",
                    ("%" + m["case_no"] + "%",),
                ).fetchall()
            if not cands:
                cands = conn.execute(
                    "SELECT invoice_no, invoice_date, buyer, total_amount, case_no FROM invoice "
                    "WHERE total_amount > 0 AND invoice_no NOT IN "
                    "(SELECT DISTINCT invoice_no FROM prepayment_offset) ORDER BY invoice_date LIMIT 200"
                ).fetchall()
            for c in cands:
                combo.addItem(
                    f"{c['invoice_date']} {c['invoice_no']} {c['buyer'][:14]} {c['total_amount']:,.2f}",
                    c["invoice_no"],
                )
        finally:
            conn.close()
        if combo.count() == 0:
            QMessageBox.information(self, "提示", "没有可核销的发票")
            return
        form.addRow("核销到发票", combo)

        amt = QDoubleSpinBox()
        amt.setRange(0.01, 99999999)
        amt.setDecimals(2)
        amt.setValue(max(m["remain"], 0.0))
        form.addRow("核销金额", amt)

        date = QDateEdit()
        date.setCalendarPopup(True)
        date.setDisplayFormat("yyyy-MM")
        date.setDate(date.date().currentDate())
        form.addRow("核销日期(月)", date)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        inv_no = combo.currentData()
        amount = amt.value()
        ym = date.date().toString("yyyy-MM")
        if amount <= 0 or amount > m["remain"] + 0.01:
            QMessageBox.warning(self, "提示", "核销金额超出剩余余额")
            return

        conn = get_conn()
        try:
            # 核销记录
            conn.execute(
                "INSERT INTO prepayment_offset (prepayment_id, invoice_no, offset_amount, offset_date) VALUES (?,?,?,?)",
                (m["id"], inv_no, amount, ym),
            )
            # 发票已收金额（source='manual'：核销为手动确认动作，撤销导入不受影响）
            inv = conn.execute("SELECT invoice_date, total_amount FROM invoice WHERE invoice_no=?", (inv_no,)).fetchone()
            receipt_date = ym if not inv else inv["invoice_date"][:7]
            conn.execute(
                "INSERT INTO collection (invoice_no, amount, receipt_date, source, note) VALUES (?,?,?,?,?)",
                (inv_no, amount, receipt_date, "manual", f"预收款核销（{m['buyer']}）"),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            QMessageBox.critical(self, "核销失败", str(e))
            return
        finally:
            conn.close()
        self.refresh()

    # ---- 核销明细 ----
    def view_offsets(self) -> None:
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT o.invoice_no, o.offset_amount, o.offset_date, p.buyer
                   FROM prepayment_offset o JOIN prepayment p ON o.prepayment_id=p.id
                   ORDER BY o.offset_date DESC"""
            ).fetchall()
        finally:
            conn.close()
        dlg = QDialog(self)
        dlg.setWindowTitle("核销明细")
        dlg.resize(560, 380)
        lay = QVBoxLayout(dlg)
        t = QTableWidget(len(rows), 4)
        t.setHorizontalHeaderLabels(["发票号码", "核销金额", "核销日期", "预收款方"])
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        for r, row in enumerate(rows):
            vals = [row["invoice_no"], row["offset_amount"], row["offset_date"], row["buyer"]]
            for c, v in enumerate(vals):
                t.setItem(r, c, QTableWidgetItem("" if v is None else
                                                 (f"{v:,.2f}" if isinstance(v, float) else str(v))))
        lay.addWidget(t)
        dlg.exec()
