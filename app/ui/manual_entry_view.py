"""手动补录：发票 / 收款 / 退款（source='manual'，参与全部计算，可按来源筛选）"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from qfluentwidgets import (SubtitleLabel, CaptionLabel, PrimaryPushButton, PushButton)
from app.db import get_conn
from app.importer.parse_handler import parse_handler_column


class ManualEntryView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        t = SubtitleLabel("手动补录")
        lay.addWidget(t)
        h = QLabel("补录历史发票 / 收款 / 退款（如跨年红冲原票）。补录数据与导入数据同模型计算。")
        h.setStyleSheet("color:#8A8886;")
        lay.addWidget(h)

        btns = QHBoxLayout()
        self.btn_invoice = QPushButton("添加发票")
        self.btn_invoice.clicked.connect(self.add_invoice)
        self.btn_collection = QPushButton("添加收款")
        self.btn_collection.clicked.connect(self.add_collection)
        self.btn_refund = QPushButton("添加退款")
        self.btn_refund.clicked.connect(self.add_refund)
        for b in (self.btn_invoice, self.btn_collection, self.btn_refund):
            b.setObjectName("primary" if b is self.btn_invoice else "")
            btns.addWidget(b)
        btns.addStretch()
        lay.addLayout(btns)

        self.tabs = QTabWidget()
        self.tab_invoices = self._make_tab("发票")
        self.tab_collections = self._make_tab("收款")
        self.tab_refunds = self._make_tab("退款")
        self.tabs.addTab(self.tab_invoices, "补录发票")
        self.tabs.addTab(self.tab_collections, "补录收款")
        self.tabs.addTab(self.tab_refunds, "补录退款")
        lay.addWidget(self.tabs, 1)

        self.refresh()

    def _make_tab(self, kind: str) -> QTableWidget:
        cols = {"发票": ["开票日期", "发票号码", "购方名称", "价税合计", "经办人", "案号"],
                "收款": ["发票号码", "收款金额", "收款日期", "备注"],
                "退款": ["红字发票", "退款金额", "退款日期"]}[kind]
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        return t

    def refresh(self) -> None:
        conn = get_conn()
        try:
            invs = conn.execute(
                "SELECT invoice_no, invoice_date, buyer, total_amount, case_no FROM invoice "
                "WHERE source='manual' ORDER BY invoice_date"
            ).fetchall()
            cols = conn.execute(
                "SELECT invoice_no, amount, receipt_date, note FROM collection WHERE source='manual' ORDER BY receipt_date"
            ).fetchall()
            refs = conn.execute("SELECT red_invoice_no, refund_amount, refund_date FROM refund ORDER BY refund_date").fetchall()
        finally:
            conn.close()
        self._fill(self.tab_invoices, invs, 5)
        self._fill(self.tab_collections, cols, 4)
        self._fill(self.tab_refunds, refs, 3)

    @staticmethod
    def _fill(tbl: QTableWidget, rows, ncols: int) -> None:
        tbl.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c in range(ncols):
                v = row[c] if c < len(row) else None
                tbl.setItem(r, c, QTableWidgetItem(
                    "" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v))))

    # ---- 添加发票 ----
    def add_invoice(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("添加发票（手动补录）")
        form = QFormLayout(dlg)
        no_edit = QLineEdit(); date_edit = QLineEdit(); buyer_edit = QLineEdit()
        amt = QDoubleSpinBox(); amt.setRange(-99999999, 99999999); amt.setDecimals(2)
        handler_edit = QLineEdit(); handler_edit.setPlaceholderText("如：张三4000、李四5000（红字填正数即可）")
        case_edit = QLineEdit()
        form.addRow("发票号码", no_edit)
        form.addRow("开票日期(YYYY-MM-DD)", date_edit)
        form.addRow("购方名称", buyer_edit)
        form.addRow("价税合计", amt)
        form.addRow("经办人及金额", handler_edit)
        form.addRow("案号", case_edit)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        no = no_edit.text().strip()
        if not no:
            QMessageBox.warning(self, "提示", "发票号码不能为空"); return
        total = amt.value()
        handlers = parse_handler_column(handler_edit.text(), total, no) if handler_edit.text().strip() else []
        conn = get_conn()
        try:
            if conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (no,)).fetchone():
                QMessageBox.warning(self, "提示", f"发票 {no} 已存在"); return
            conn.execute(
                "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, case_no, source) VALUES (?,?,?,?,?,?)",
                (no, date_edit.text().strip() or None, buyer_edit.text().strip(), total, case_edit.text().strip(), "manual"),
            )
            for name, amount in handlers:
                conn.execute(
                    "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source) VALUES (?,?,?,?)",
                    (no, name, amount, "manual"),
                )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback(); QMessageBox.critical(self, "失败", str(e)); return
        finally:
            conn.close()
        self.refresh()

    # ---- 添加收款 ----
    def add_collection(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("添加收款（手动补录）")
        form = QFormLayout(dlg)
        combo = QComboBox()
        conn = get_conn()
        try:
            for r in conn.execute("SELECT invoice_no, buyer FROM invoice WHERE total_amount>=0 ORDER BY invoice_date"):
                combo.addItem(f"{r['invoice_no']} {r['buyer'][:12]}", r["invoice_no"])
        finally:
            conn.close()
        amt = QDoubleSpinBox(); amt.setRange(0.01, 99999999); amt.setDecimals(2)
        date = QDateEdit(); date.setCalendarPopup(True); date.setDisplayFormat("yyyy-MM")
        form.addRow("发票", combo)
        form.addRow("收款金额", amt)
        form.addRow("收款日期(月)", date)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO collection (invoice_no, amount, receipt_date, source, note) VALUES (?,?,?,?,?)",
                (combo.currentData(), amt.value(), date.date().toString("yyyy-MM"), "manual", "手动补录"),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback(); QMessageBox.critical(self, "失败", str(e)); return
        finally:
            conn.close()
        self.refresh()

    # ---- 添加退款 ----
    def add_refund(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("添加退款（手动补录）")
        form = QFormLayout(dlg)
        combo = QComboBox()
        conn = get_conn()
        try:
            for r in conn.execute("SELECT invoice_no, orig_invoice_no FROM invoice WHERE total_amount<0 ORDER BY invoice_date"):
                combo.addItem(f"{r['invoice_no']} 原票:{r['orig_invoice_no']}", r["invoice_no"])
        finally:
            conn.close()
        amt = QDoubleSpinBox(); amt.setRange(0.01, 99999999); amt.setDecimals(2)
        date = QDateEdit(); date.setCalendarPopup(True); date.setDisplayFormat("yyyy-MM")
        form.addRow("红字发票", combo)
        form.addRow("退款金额", amt)
        form.addRow("退款日期(月)", date)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        no = combo.currentData()
        conn = get_conn()
        try:
            orig = conn.execute("SELECT orig_invoice_no FROM invoice WHERE invoice_no=?", (no,)).fetchone()
            conn.execute(
                "INSERT INTO refund (red_invoice_no, orig_invoice_no, refund_amount, refund_date) VALUES (?,?,?,?)",
                (no, orig["orig_invoice_no"] if orig else None, amt.value(), date.date().toString("yyyy-MM")),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback(); QMessageBox.critical(self, "失败", str(e)); return
        finally:
            conn.close()
        self.refresh()
