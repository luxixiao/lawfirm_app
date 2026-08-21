"""手动补录：发票 / 收款 / 退款（source='manual'，参与全部计算，可按来源筛选）"""
from __future__ import annotations

from PySide6.QtCore import Qt, QDate
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
        self.btn_edit = QPushButton("编辑所选补录发票")
        self.btn_edit.setObjectName("primary")
        self.btn_edit.clicked.connect(self.edit_invoice)
        self.btn_collection = QPushButton("添加收款")
        self.btn_collection.clicked.connect(self.add_collection)
        self.btn_refund = QPushButton("添加退款")
        self.btn_refund.clicked.connect(self.add_refund)
        self.btn_prefill = QPushButton("补录待补录原票")
        self.btn_prefill.clicked.connect(self.add_pending_orig)
        for b in (self.btn_invoice, self.btn_edit, self.btn_collection, self.btn_refund, self.btn_prefill):
            btns.addWidget(b)
        btns.addStretch()
        lay.addLayout(btns)

        self.tabs = QTabWidget()
        self.tab_pending = self._make_tab("待补录")
        self.tab_invoices = self._make_tab("发票")
        self.tab_collections = self._make_tab("收款")
        self.tab_refunds = self._make_tab("退款")
        self.tabs.addTab(self.tab_pending, "待补录原票")
        self.tabs.addTab(self.tab_invoices, "补录发票")
        self.tabs.addTab(self.tab_collections, "补录收款")
        self.tabs.addTab(self.tab_refunds, "补录退款")
        # 双击已补录发票 → 编辑
        self.tab_invoices.cellDoubleClicked.connect(lambda *_: self.edit_invoice())
        lay.addWidget(self.tabs, 1)

        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        """切换到本页时自动刷新数据"""
        super().showEvent(event)
        self.refresh()

    def _make_tab(self, kind: str) -> QTableWidget:
        cols = {"待补录": ["红字发票", "红字金额", "原票号码", "应退金额", "购方名称"],
                "发票": ["开票日期", "发票号码", "购方名称", "价税合计", "经办人", "案号"],
                "收款": ["发票号码", "收款金额", "收款日期", "备注"],
                "退款": ["红字发票", "退款金额", "退款日期"]}[kind]
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        return t

    def refresh(self) -> None:
        conn = get_conn()
        try:
            # 字段顺序与表格列一致：开票日期|发票号码|购方名称|价税合计|经办人|案号
            invs = conn.execute(
                """SELECT i.invoice_date, i.invoice_no, i.buyer, i.total_amount,
                          (SELECT group_concat(cd.person_name, '、') FROM charge_detail cd
                           WHERE cd.invoice_no = i.invoice_no) AS handlers,
                          i.case_no
                   FROM invoice i WHERE i.source='manual' ORDER BY i.invoice_date"""
            ).fetchall()
            cols = conn.execute(
                "SELECT invoice_no, amount, receipt_date, note FROM collection WHERE source='manual' ORDER BY receipt_date"
            ).fetchall()
            refs = conn.execute("SELECT red_invoice_no, refund_amount, refund_date FROM refund ORDER BY refund_date").fetchall()
        finally:
            conn.close()
        self._fill(self.tab_invoices, invs, 6)
        self._fill(self.tab_collections, cols, 4)
        self._fill(self.tab_refunds, refs, 3)
        self._load_pending()

    # ---- 待补录原票列表 ----
    def _load_pending(self) -> None:
        from app.engine.refund import evaluate_red_invoices
        conn = get_conn()
        try:
            rows = []
            for m in evaluate_red_invoices(conn):
                if m["status"] != "orig_missing":
                    continue
                inv = conn.execute(
                    "SELECT buyer, case_no FROM invoice WHERE invoice_no=?", (m["red_invoice_no"],)
                ).fetchone()
                rows.append([
                    m["red_invoice_no"], m["red_amount"], m["orig_invoice_no"] or "—",
                    m["refund_amount"], inv["buyer"] if inv else "", m["red_invoice_no"],
                ])
        finally:
            conn.close()
        self.tab_pending.setRowCount(len(rows))
        self._pending_meta = {}
        for r, row in enumerate(rows):
            for c in range(5):
                v = row[c]
                item = QTableWidgetItem("" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                self.tab_pending.setItem(r, c, item)
            self._pending_meta[r] = row[5]  # 红字发票号

    # ---- 补录待补录原票（预填红字发票已知信息）----
    def add_pending_orig(self) -> None:
        row = self.tab_pending.currentRow()
        if row < 0:
            # 无选中时给提示并列出可选
            if self.tab_pending.rowCount() == 0:
                QMessageBox.information(self, "提示", "当前没有需要补录的原票（退款页显示'原票未导入'的发票）")
                return
            QMessageBox.information(self, "提示", "请先在列表中选择一行（红字发票）再补录")
            return
        red_no = self._pending_meta.get(row)
        if not red_no:
            return
        conn = get_conn()
        try:
            inv = conn.execute("SELECT * FROM invoice WHERE invoice_no=?", (red_no,)).fetchone()
            cds = conn.execute(
                "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=? ORDER BY id", (red_no,)
            ).fetchall()
        finally:
            conn.close()
        if inv is None:
            return
        # 经办人文本（负号转正，供原票预填）
        parts = []
        for cd in cds:
            amt = abs(cd["billing_amount"])
            parts.append(f"{cd['person_name']}{amt:g}")
        self.add_invoice(prefill={
            "no": inv["orig_invoice_no"] or "",
            "buyer": inv["buyer"],
            "amount": abs(inv["total_amount"]),   # 原票金额 ≥ 红字绝对值（参考值）
            "handler": "、".join(parts),
            "case": inv["case_no"],
        })

    # ---- 编辑已补录发票（含收款）----
    def edit_invoice(self) -> None:
        row = self.tab_invoices.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先在「补录发票」列表中选择一行（或双击）")
            return
        no_item = self.tab_invoices.item(row, 1)
        if not no_item:
            return
        no = no_item.text().strip()

        conn = get_conn()
        try:
            inv = conn.execute("SELECT * FROM invoice WHERE invoice_no=?", (no,)).fetchone()
            cds = conn.execute(
                "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=? ORDER BY id", (no,)
            ).fetchall()
            recs = conn.execute(
                "SELECT amount, receipt_date FROM collection WHERE invoice_no=? AND source='manual' ORDER BY id", (no,)
            ).fetchall()
        finally:
            conn.close()
        if inv is None:
            return
        # 经办人文本（负数转正，红字补录场景）
        parts = [f"{cd['person_name']}{abs(cd['billing_amount']):g}" for cd in cds]

        from PySide6.QtWidgets import (QDoubleSpinBox, QTableWidgetItem, QVBoxLayout,
                                       QFormLayout, QDialogButtonBox)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑补录发票：{no}")
        dlg.resize(560, 620)
        lay = QVBoxLayout(dlg)

        form = QFormLayout()
        no_lbl = QLabel(no)
        date_edit = QLineEdit(inv["invoice_date"] or "")
        buyer_edit = QLineEdit(inv["buyer"] or "")
        amt = QDoubleSpinBox(); amt.setRange(-99999999, 99999999); amt.setDecimals(2)
        amt.setValue(inv["total_amount"])
        handler_edit = QLineEdit("、".join(parts))
        handler_edit.setPlaceholderText("如：张三4000、李四5000")
        case_edit = QLineEdit(inv["case_no"] or "")
        form.addRow("发票号码", no_lbl)
        form.addRow("开票日期(YYYY-MM-DD)", date_edit)
        form.addRow("购方名称", buyer_edit)
        form.addRow("价税合计", amt)
        form.addRow("经办人及金额", handler_edit)
        form.addRow("案号", case_edit)
        lay.addLayout(form)

        rec_title = SubtitleLabel("收款信息（保存后按下列记录重建）")
        lay.addWidget(rec_title)
        rec_table = QTableWidget(0, 2)
        rec_table.setHorizontalHeaderLabels(["收款金额", "收款日期(YYYY-MM)"])
        rec_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        rec_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        rec_table.verticalHeader().setVisible(False)
        rec_table.horizontalHeader().setStretchLastSection(True)
        for r_, (a, d) in enumerate(recs):
            rec_table.insertRow(r_)
            ai = QTableWidgetItem(f"{a:,.2f}")
            ai.setData(Qt.ItemDataRole.UserRole, round(a, 2))
            rec_table.setItem(r_, 0, ai)
            rec_table.setItem(r_, 1, QTableWidgetItem(d))
        lay.addWidget(rec_table, 1)

        rec_btns = QHBoxLayout()
        btn_add = PushButton("添加收款")
        btn_del = PushButton("删除所选")
        rec_btns.addWidget(btn_add)
        rec_btns.addWidget(btn_del)
        rec_btns.addStretch()
        lay.addLayout(rec_btns)

        def add_rec():
            sub = QDialog(dlg)
            sub.setWindowTitle("添加收款")
            f2 = QFormLayout(sub)
            s_amt = QDoubleSpinBox(); s_amt.setRange(0.01, 99999999); s_amt.setDecimals(2)
            s_date = QDateEdit(); s_date.setCalendarPopup(True); s_date.setDisplayFormat("yyyy-MM")
            s_date.setDate(QDate.currentDate())
            f2.addRow("收款金额", s_amt)
            f2.addRow("收款日期(月)", s_date)
            b2 = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            b2.accepted.connect(sub.accept); b2.rejected.connect(sub.reject)
            f2.addRow(b2)
            if sub.exec() != QDialog.DialogCode.Accepted:
                return
            r_ = rec_table.rowCount()
            rec_table.insertRow(r_)
            ai = QTableWidgetItem(f"{s_amt.value():,.2f}")
            ai.setData(Qt.ItemDataRole.UserRole, round(s_amt.value(), 2))
            rec_table.setItem(r_, 0, ai)
            rec_table.setItem(r_, 1, QTableWidgetItem(s_date.date().toString("yyyy-MM")))

        def del_rec():
            r_ = rec_table.currentRow()
            if r_ >= 0:
                rec_table.removeRow(r_)
            else:
                QMessageBox.information(dlg, "提示", "请先选择要删除的收款行")

        btn_add.clicked.connect(add_rec)
        btn_del.clicked.connect(del_rec)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        total = amt.value()
        handlers = parse_handler_column(handler_edit.text(), total, no) if handler_edit.text().strip() else []
        receipts = []
        for r_ in range(rec_table.rowCount()):
            a_item = rec_table.item(r_, 0)
            d_item = rec_table.item(r_, 1)
            if a_item and d_item:
                receipts.append((a_item.data(Qt.ItemDataRole.UserRole), d_item.text()))
        if total > 0 and sum(a for a, _ in receipts) > total + 0.01:
            QMessageBox.warning(self, "提示", "收款合计超过开票金额")
            return

        conn = get_conn()
        try:
            # 发票基础信息
            conn.execute(
                "UPDATE invoice SET invoice_date=?, buyer=?, total_amount=?, case_no=? WHERE invoice_no=?",
                (date_edit.text().strip() or None, buyer_edit.text().strip(), total,
                 case_edit.text().strip(), no),
            )
            # 经办人：删旧插新
            conn.execute("DELETE FROM charge_detail WHERE invoice_no=?", (no,))
            for name, amount in handlers:
                conn.execute(
                    "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source) VALUES (?,?,?,?)",
                    (no, name, amount, "manual"),
                )
            # 收款：重建（仅 manual 来源；导入来源收款不受影响）
            conn.execute("DELETE FROM collection WHERE invoice_no=? AND source='manual'", (no,))
            for amount, ym in receipts:
                conn.execute(
                    "INSERT INTO collection (invoice_no, amount, receipt_date, source, note) VALUES (?,?,?,?,?)",
                    (no, amount, ym, "manual", "手动补录"),
                )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback(); QMessageBox.critical(self, "失败", str(e)); return
        finally:
            conn.close()
        self.refresh()
        QMessageBox.information(self, "已保存", f"发票 {no} 已更新")

    @staticmethod
    def _fill(tbl: QTableWidget, rows, ncols: int) -> None:
        tbl.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c in range(ncols):
                v = row[c] if c < len(row) else None
                tbl.setItem(r, c, QTableWidgetItem(
                    "" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v))))

    # ---- 添加发票（含收款信息）----
    def add_invoice(self, prefill: dict | None = None) -> None:
        prefill = prefill or {}
        from PySide6.QtWidgets import (QDoubleSpinBox, QTableWidgetItem, QVBoxLayout,
                                       QFormLayout, QDialogButtonBox, QDateEdit)
        from PySide6.QtCore import QDate

        dlg = QDialog(self)
        dlg.setWindowTitle("添加发票（手动补录）")
        dlg.resize(560, 620)
        lay = QVBoxLayout(dlg)

        form = QFormLayout()
        no_edit = QLineEdit(); date_edit = QLineEdit(); buyer_edit = QLineEdit()
        amt = QDoubleSpinBox(); amt.setRange(-99999999, 99999999); amt.setDecimals(2)
        handler_edit = QLineEdit(); handler_edit.setPlaceholderText("如：张三4000、李四5000（红字填正数即可）")
        case_edit = QLineEdit()
        # 预填已有基础信息（补录原票场景）
        if prefill.get("no"):
            no_edit.setText(prefill["no"])
            no_edit.setReadOnly(True)  # 原票号码来自红字备注，锁定
        if prefill.get("buyer"):
            buyer_edit.setText(prefill["buyer"])
        if prefill.get("amount") is not None:
            amt.setValue(prefill["amount"])
        if prefill.get("handler"):
            handler_edit.setText(prefill["handler"])
        if prefill.get("case"):
            case_edit.setText(prefill["case"])
        form.addRow("发票号码", no_edit)
        form.addRow("开票日期(YYYY-MM-DD)", date_edit)
        form.addRow("购方名称", buyer_edit)
        form.addRow("价税合计", amt)
        form.addRow("经办人及金额", handler_edit)
        form.addRow("案号", case_edit)
        lay.addLayout(form)

        # ---- 收款信息（可多笔，可选）----
        from qfluentwidgets import SubtitleLabel, CaptionLabel
        rec_title = SubtitleLabel("收款信息（可选，可多笔）")
        lay.addWidget(rec_title)
        rec_hint = CaptionLabel("全额收款填一笔（金额=开票金额）；部分收款可分多笔。红字发票无需填收款（走退款）。")
        rec_hint.setStyleSheet("color:#8A8886;")
        lay.addWidget(rec_hint)

        rec_table = QTableWidget(0, 2)
        rec_table.setHorizontalHeaderLabels(["收款金额", "收款日期(YYYY-MM)"])
        rec_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        rec_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        rec_table.verticalHeader().setVisible(False)
        rec_table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(rec_table, 1)

        rec_btns = QHBoxLayout()
        btn_add_rec = PushButton("添加收款")
        btn_del_rec = PushButton("删除所选")
        rec_btns.addWidget(btn_add_rec)
        rec_btns.addWidget(btn_del_rec)
        rec_btns.addStretch()
        lay.addLayout(rec_btns)

        def add_receipt():
            """弹窗输入一笔收款（金额+日期）"""
            sub = QDialog(dlg)
            sub.setWindowTitle("添加收款")
            f2 = QFormLayout(sub)
            s_amt = QDoubleSpinBox(); s_amt.setRange(0.01, 99999999); s_amt.setDecimals(2)
            s_date = QDateEdit(); s_date.setCalendarPopup(True); s_date.setDisplayFormat("yyyy-MM")
            s_date.setDate(QDate.currentDate())
            f2.addRow("收款金额", s_amt)
            f2.addRow("收款日期(月)", s_date)
            btns2 = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            btns2.accepted.connect(sub.accept); btns2.rejected.connect(sub.reject)
            f2.addRow(btns2)
            if sub.exec() != QDialog.DialogCode.Accepted:
                return
            r = rec_table.rowCount()
            rec_table.insertRow(r)
            amt_item = QTableWidgetItem(f"{s_amt.value():,.2f}")
            amt_item.setData(Qt.ItemDataRole.UserRole, round(s_amt.value(), 2))
            date_item = QTableWidgetItem(s_date.date().toString("yyyy-MM"))
            rec_table.setItem(r, 0, amt_item)
            rec_table.setItem(r, 1, date_item)

        def del_receipt():
            row = rec_table.currentRow()
            if row >= 0:
                rec_table.removeRow(row)
            else:
                QMessageBox.information(dlg, "提示", "请先选择要删除的收款行")

        btn_add_rec.clicked.connect(add_receipt)
        btn_del_rec.clicked.connect(del_receipt)

        # 红字发票提示：收款走退款
        def on_total_changed():
            rec_hint.setText("红字发票（价税合计为负）收款走「退款」页确认，无需在此填收款。" if amt.value() < 0
                             else "全额收款填一笔（金额=开票金额）；部分收款可分多笔。")
        amt.valueChanged.connect(on_total_changed)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        no = no_edit.text().strip()
        if not no:
            QMessageBox.warning(self, "提示", "发票号码不能为空"); return
        total = amt.value()
        handlers = parse_handler_column(handler_edit.text(), total, no) if handler_edit.text().strip() else []

        # 收集收款记录
        receipts = []
        for r in range(rec_table.rowCount()):
            a_item = rec_table.item(r, 0)
            d_item = rec_table.item(r, 1)
            if a_item and d_item:
                receipts.append((a_item.data(Qt.ItemDataRole.UserRole), d_item.text()))
        if total > 0:
            sum_rec = sum(a for a, _ in receipts)
            if sum_rec > total + 0.01:
                QMessageBox.warning(self, "提示", f"收款合计({sum_rec:,.2f})超过开票金额({total:,.2f})")
                return

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
            # 同时写入收款记录（source='manual'）
            for amount, ym in receipts:
                conn.execute(
                    "INSERT INTO collection (invoice_no, amount, receipt_date, source, note) VALUES (?,?,?,?,?)",
                    (no, amount, ym, "manual", "手动补录"),
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
