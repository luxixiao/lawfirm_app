"""台账数据管理：查看/修改所有导入文档（发票/收款/费用/员工），修改记录带手动备注"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from qfluentwidgets import (CaptionLabel, PushButton, SubtitleLabel)

from app.db import get_conn
from app.engine.change_log import log_change, log_changes, fetch_log
from app.engine.expense_cat import (CATEGORIES, add_type, ensure_types, get_by_category,
                                    set_category, sync_from_ledger)
from app.importer.parse_handler import parse_handler_column


def _note_input(parent: QWidget) -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText("可选：填写本次修改的原因说明（记录在修改记录中）")
    return e


class LedgerView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        t = SubtitleLabel("台账数据")
        lay.addWidget(t)
        h = CaptionLabel("查看并修改所有导入/补录的台账数据；任何修改都会记录（含手动备注），修改后以新数据参与全部计算。")
        h.setStyleSheet("color:#8A8886;")
        lay.addWidget(h)

        self.tabs = QTabWidget()
        self.tab_invoice = self._make_table(["开票日期", "发票号码", "购方名称", "价税合计", "经办人", "身份", "案号", "来源"],
                                            [1, 2, 3, 4, 5, 6, 7])
        self.tab_collection = self._make_table(["发票号码", "收款金额", "收款日期", "备注", "来源"], [1, 2, 3])
        self.tab_expense = self._make_table(["账期", "经办人", "费用类型", "金额", "凭证号", "身份"], [0, 1, 2, 3, 5])
        self.tab_staff = self._make_table(["姓名", "员工类型", "是否在职"], [0, 1, 2])
        self.tab_log = self._make_table(["时间", "表", "记录", "字段", "旧值", "新值", "备注"], [], readonly=True)
        self.tabs.addTab(self.tab_invoice, "发票")
        self.tabs.addTab(self.tab_collection, "收款")
        self.tabs.addTab(self.tab_expense, "费用")
        self.tabs.addTab(self.tab_staff, "员工")
        self.tabs.addTab(self._build_cat_tab(), "费用归类")
        self.tabs.addTab(self.tab_log, "修改记录")
        lay.addWidget(self.tabs, 1)

        btns = QHBoxLayout()
        self.btn_edit = PushButton("编辑所选")
        self.btn_edit.clicked.connect(self.edit_selected)
        self.btn_batch_type = PushButton("批量设身份…")
        self.btn_batch_type.clicked.connect(self.batch_set_type)
        self.btn_refresh = PushButton("刷新")
        self.btn_refresh.clicked.connect(self.refresh)
        self.lbl = CaptionLabel("双击也可编辑")
        self.lbl.setStyleSheet("color:#8A8886;")
        btns.addWidget(self.btn_edit)
        btns.addWidget(self.btn_batch_type)
        btns.addWidget(self.btn_refresh)
        btns.addStretch()
        btns.addWidget(self.lbl)
        lay.addLayout(btns)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        self.log.setPlaceholderText("操作日志…")
        lay.addWidget(self.log)

        for tb in (self.tab_invoice, self.tab_collection, self.tab_expense, self.tab_staff):
            tb.cellDoubleClicked.connect(lambda *_: self.edit_selected())
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

    # ---- 加载 ----
    def refresh(self) -> None:
        conn = get_conn()
        try:
            invs = conn.execute(
                """SELECT i.invoice_date, i.invoice_no, i.buyer, i.total_amount,
                          (SELECT group_concat(cd.person_name || printf('%.2f', cd.billing_amount), ' ')
                           FROM charge_detail cd WHERE cd.invoice_no = i.invoice_no) AS handlers,
                          (SELECT CASE WHEN COUNT(DISTINCT cd.person_type) <= 1
                                  THEN COALESCE(MAX(cd.person_type), '') ELSE '混合' END
                           FROM charge_detail cd WHERE cd.invoice_no = i.invoice_no) AS htypes,
                          i.case_no, i.source, i.invoice_no AS key
                   FROM invoice i ORDER BY i.invoice_date, i.invoice_no"""
            ).fetchall()
            cols = conn.execute(
                "SELECT invoice_no, amount, receipt_date, note, source, id AS key FROM collection ORDER BY receipt_date"
            ).fetchall()
            exps = conn.execute(
                "SELECT period, actual_handler, expense_type, expense_amount, ticket_no, person_type, id AS key "
                "FROM expense_ledger ORDER BY period, id"
            ).fetchall()
            staffs = conn.execute(
                "SELECT name, staff_type, is_active, id AS key FROM staff ORDER BY name"
            ).fetchall()
            logs = conn.execute(
                "SELECT created_at, table_name, record_id, field, old_value, new_value, note "
                "FROM change_log ORDER BY id DESC LIMIT 500"
            ).fetchall()
        finally:
            conn.close()
        inv_display = []
        for r in invs:
            r2 = [r["invoice_date"], r["invoice_no"], r["buyer"], r["total_amount"],
                  r["handlers"], r["htypes"], r["case_no"], r["source"], r["key"]]
            inv_display.append(r2)
        self._fill(self.tab_invoice, inv_display, "invoice")
        # 发票身份列（索引5）改为可直接下拉修改（整票统一）
        for r, r2 in enumerate(inv_display):
            combo = QComboBox()
            for t in ["未标", "合伙", "聘用", "兼职"]:
                combo.addItem(t, userData=t)
            combo.setCurrentText(r2[5] or "未标")
            key = r2[-1]
            combo.currentIndexChanged.connect(
                lambda *_, k=key, c=combo: self._set_invoice_type(k, c))
            self.tab_invoice.setCellWidget(r, 5, combo)
        self._fill(self.tab_collection, cols, "collection")
        exp_display = []
        for r in exps:
            r2 = [r["period"], r["actual_handler"], r["expense_type"], r["expense_amount"],
                  r["ticket_no"], r["person_type"] or "未标", r["key"]]
            exp_display.append(r2)
        self._fill(self.tab_expense, exp_display, "expense")
        # 费用身份列（索引5）下拉修改
        for r, r2 in enumerate(exp_display):
            combo = QComboBox()
            for t in ["未标", "合伙", "聘用", "兼职"]:
                combo.addItem(t, userData=t)
            combo.setCurrentText(r2[5] or "未标")
            eid = r2[-1]
            combo.currentIndexChanged.connect(
                lambda *_, e=eid, c=combo: self._set_expense_type(e, c))
            self.tab_expense.setCellWidget(r, 5, combo)
        self._fill(self.tab_staff, staffs, "staff")
        self._fill(self.tab_log, logs, "log")
        self._load_cat()

    # ---- 编辑 ----
    def edit_selected(self) -> None:
        tb = self.tabs.currentWidget()
        if getattr(tb, "_readonly", False):
            QMessageBox.information(self, "提示", "修改记录为只读（修改时自动生成）")
            return
        row = tb.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一行（或双击）")
            return
        if tb is self.tab_invoice:
            self._edit_invoice(row)
        elif tb is self.tab_collection:
            self._edit_collection(row)
        elif tb is self.tab_expense:
            self._edit_expense(row)
        elif tb is self.tab_staff:
            self._edit_staff(row)

    # ---- 发票 ----
    def _edit_invoice(self, row: int) -> None:
        key = self._meta_invoice[row]
        conn = get_conn()
        try:
            inv = conn.execute("SELECT * FROM invoice WHERE invoice_no=?", (key,)).fetchone()
            cds = conn.execute(
                "SELECT person_name, billing_amount, person_type FROM charge_detail WHERE invoice_no=? ORDER BY id", (key,)
            ).fetchall()
        finally:
            conn.close()
        if inv is None:
            return
        parts = []
        for cd in cds:
            pt = cd["person_type"] or ""
            parts.append(f"{cd['person_name']}{abs(cd['billing_amount']):g}({pt})" if pt
                         else f"{cd['person_name']}{abs(cd['billing_amount']):g}")

        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑发票：{key}")
        dlg.resize(460, 400)
        form = QFormLayout(dlg)
        date_edit = QLineEdit(inv["invoice_date"] or "")
        buyer_edit = QLineEdit(inv["buyer"] or "")
        amt = QDoubleSpinBox(); amt.setRange(-99999999, 99999999); amt.setDecimals(2); amt.setValue(inv["total_amount"])
        handler_edit = QLineEdit("、".join(parts))
        handler_edit.setPlaceholderText("如：张三4000(合伙)、李四5000(聘用)；不带身份则保持原值")
        case_edit = QLineEdit(inv["case_no"] or "")
        # 身份：整票统一（取首个经办人身份）
        type_combo = QComboBox()
        for t in ["未标", "合伙", "聘用", "兼职"]:
            type_combo.addItem(t, userData=t)
        cur_type = (cds[0]["person_type"] if cds and cds[0]["person_type"] else "未标")
        type_combo.setCurrentText(cur_type)
        note_edit = _note_input(self)
        form.addRow("开票日期", date_edit)
        form.addRow("购方名称", buyer_edit)
        form.addRow("价税合计", amt)
        form.addRow("经办人及金额", handler_edit)
        form.addRow("身份(整票统一)", type_combo)
        form.addRow("案号", case_edit)
        form.addRow("修改备注", note_edit)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        total = amt.value()
        note = note_edit.text().strip()
        new_handlers = parse_handler_column(handler_edit.text(), total, key, with_type=True) if handler_edit.text().strip() else []
        conn = get_conn()
        try:
            changes = []
            for f, o, n in [("invoice_date", inv["invoice_date"], date_edit.text().strip() or None),
                            ("buyer", inv["buyer"], buyer_edit.text().strip()),
                            ("total_amount", inv["total_amount"], total),
                            ("case_no", inv["case_no"], case_edit.text().strip())]:
                if str(o or "") != str(n or ""):
                    changes.append((f, o, n))
            conn.execute(
                "UPDATE invoice SET invoice_date=?, buyer=?, total_amount=?, case_no=? WHERE invoice_no=?",
                (date_edit.text().strip() or None, buyer_edit.text().strip(), total,
                 case_edit.text().strip(), key),
            )
            # 经办人重建（比较旧列表；身份逐经办人保留）
            old_handlers = [(cd["person_name"], cd["billing_amount"]) for cd in cds]
            if sorted(str(x[:2]) for x in old_handlers) != sorted(str(x[:2]) for x in new_handlers):
                changes.append(("charge_detail", ";".join(map(str, old_handlers)), ";".join(map(str, new_handlers))))
                conn.execute("DELETE FROM charge_detail WHERE invoice_no=?", (key,))
                for name, amount, pt in new_handlers:
                    conn.execute(
                        "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source, person_type) VALUES (?,?,?,?,?)",
                        (key, name, amount, inv["source"], pt or (type_combo.currentData() if type_combo.currentData() != "未标" else "")),
                    )
            else:
                # 身份逐经办人更新（new_handlers 带身份）
                for (name, amount, pt), (old_name, old_amt, old_pt) in zip(
                        new_handlers, [(cd["person_name"], cd["billing_amount"], cd["person_type"] or "") for cd in cds]):
                    if (old_pt or "") != (pt or ""):
                        changes.append((f"person_type[{name}]", old_pt or "未标", pt or "未标"))
                        conn.execute(
                            "UPDATE charge_detail SET person_type=? WHERE invoice_no=? AND person_name=?",
                            (pt, key, name))
            log_changes(conn, "invoice", key, changes, note)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback(); QMessageBox.critical(self, "失败", str(e)); return
        finally:
            conn.close()
        self.refresh()
        QMessageBox.information(self, "已保存", f"发票 {key} 已修改，修改已记录")

    # ---- 收款 ----
    def _edit_collection(self, row: int) -> None:
        cid = self._meta_collection[row]
        conn = get_conn()
        try:
            rec = conn.execute("SELECT * FROM collection WHERE id=?", (cid,)).fetchone()
        finally:
            conn.close()
        if rec is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑收款 #{cid}")
        form = QFormLayout(dlg)
        amt = QDoubleSpinBox(); amt.setRange(-99999999, 99999999); amt.setDecimals(2); amt.setValue(rec["amount"])
        date_edit = QLineEdit(rec["receipt_date"] or "")
        note_edit = QLineEdit(rec["note"] or "")
        rnote = _note_input(self)
        form.addRow("收款金额", amt)
        form.addRow("收款日期(YYYY-MM)", date_edit)
        form.addRow("备注", note_edit)
        form.addRow("修改备注", rnote)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        conn = get_conn()
        try:
            changes = []
            for f, o, n in [("amount", rec["amount"], amt.value()),
                            ("receipt_date", rec["receipt_date"], date_edit.text().strip() or None),
                            ("note", rec["note"], note_edit.text().strip())]:
                if str(o or "") != str(n or ""):
                    changes.append((f, o, n))
            if not changes:
                conn.close(); return
            conn.execute("UPDATE collection SET amount=?, receipt_date=?, note=? WHERE id=?",
                         (amt.value(), date_edit.text().strip() or None, note_edit.text().strip(), cid))
            log_changes(conn, "collection", str(cid), changes, rnote.text().strip())
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback(); QMessageBox.critical(self, "失败", str(e)); return
        finally:
            conn.close()
        self.refresh()

    # ---- 费用 ----
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
        rnote = _note_input(self)
        type_combo2 = QComboBox()
        for t in ["合伙", "聘用", "兼职", "未标"]:
            type_combo2.addItem(t, userData=t)
        type_combo2.setCurrentText(e["person_type"] or "未标")
        form.addRow("费用类型", type_edit)
        form.addRow("金额", amt)
        form.addRow("经办人", handler_edit)
        form.addRow("身份", type_combo2)
        form.addRow("修改备注", rnote)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        conn = get_conn()
        try:
            changes = []
            for f, o, n in [("expense_type", e["expense_type"], type_edit.text().strip()),
                            ("expense_amount", e["expense_amount"], amt.value()),
                            ("actual_handler", e["actual_handler"], handler_edit.text().strip()),
                            ("person_type", e["person_type"] or "未标", type_combo2.currentData())]:
                if str(o or "") != str(n or ""):
                    changes.append((f, o, n))
            if not changes:
                conn.close(); return
            conn.execute("UPDATE expense_ledger SET expense_type=?, expense_amount=?, actual_handler=?, person_type=? WHERE id=?",
                         (type_edit.text().strip(), amt.value(), handler_edit.text().strip(),
                          type_combo2.currentData(), eid))
            log_changes(conn, "expense_ledger", str(eid), changes, rnote.text().strip())
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback(); QMessageBox.critical(self, "失败", str(e)); return
        finally:
            conn.close()
        self.refresh()

    # ---- 员工 ----
    def _edit_staff(self, row: int) -> None:
        sid = self._meta_staff[row]
        conn = get_conn()
        try:
            s = conn.execute("SELECT * FROM staff WHERE id=?", (sid,)).fetchone()
        finally:
            conn.close()
        if s is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑员工：{s['name']}")
        form = QFormLayout(dlg)
        type_combo = QComboBox()
        for t in ["合伙", "聘用", "兼职", "其他"]:
            type_combo.addItem(t, userData=t)
        idx = max(0, [type_combo.itemText(i) for i in range(type_combo.count())].index(
            (s["staff_type"] or "其他") if (s["staff_type"] or "其他") in [type_combo.itemText(i) for i in range(type_combo.count())] else "其他"))
        type_combo.setCurrentIndex(idx)
        active_combo = QComboBox()
        active_combo.addItem("在职", userData=1)
        active_combo.addItem("离职", userData=0)
        active_combo.setCurrentIndex(0 if s["is_active"] else 1)
        rnote = _note_input(self)
        form.addRow("员工类型", type_combo)
        form.addRow("状态", active_combo)
        form.addRow("修改备注", rnote)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        conn = get_conn()
        try:
            changes = []
            for f, o, n in [("staff_type", s["staff_type"], type_combo.currentData()),
                            ("is_active", s["is_active"], active_combo.currentData())]:
                if str(o or "") != str(n or ""):
                    changes.append((f, o, n))
            if not changes:
                conn.close(); return
            conn.execute("UPDATE staff SET staff_type=?, is_active=? WHERE id=?",
                         (type_combo.currentData(), active_combo.currentData(), sid))
            log_changes(conn, "staff", s["name"], changes, rnote.text().strip())
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback(); QMessageBox.critical(self, "失败", str(e)); return
        finally:
            conn.close()
        self.refresh()

    # ---- 费用归类 Tab（类型全集 + 归类维护 + 新增）----
    def _build_cat_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 10, 8, 10)
        v.setSpacing(8)
        self.cat_table = QTableWidget(0, 3)
        self.cat_table.setHorizontalHeaderLabels(["费用类型", "归类", "说明"])
        self.cat_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.cat_table.verticalHeader().setVisible(False)
        self.cat_table.horizontalHeader().setStretchLastSection(True)
        self.cat_table.setColumnWidth(0, 200)
        self.cat_table.setColumnWidth(1, 130)
        v.addWidget(self.cat_table, 1)
        bar = QHBoxLayout()
        bar.addWidget(CaptionLabel("新增类型"))
        self.cat_new = QLineEdit()
        self.cat_new.setPlaceholderText("费用类型名称")
        self.cat_new.setMaximumWidth(200)
        bar.addWidget(self.cat_new)
        self.cat_new_combo = QComboBox()
        for c in CATEGORIES:
            self.cat_new_combo.addItem(c, userData=c)
        bar.addWidget(self.cat_new_combo)
        btn_add = PushButton("添加")
        btn_add.clicked.connect(self._cat_add)
        bar.addWidget(btn_add)
        bar.addStretch()
        v.addLayout(bar)
        return w

    def _load_cat(self) -> None:
        sync_from_ledger()
        conn = get_conn()
        try:
            rows = conn.execute("SELECT expense_type, category FROM expense_cat ORDER BY category, expense_type").fetchall()
        finally:
            conn.close()
        self.cat_table.setRowCount(0)
        self.cat_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            t_item = QTableWidgetItem(row["expense_type"])
            t_item.setData(Qt.ItemDataRole.UserRole, row["expense_type"])
            self.cat_table.setItem(r, 0, t_item)
            combo = QComboBox()
            for c in CATEGORIES:
                combo.addItem(c, userData=c)
            combo.setCurrentText(row["category"] or "其他")
            combo.currentIndexChanged.connect(
                lambda *_, row=r, etype=row["expense_type"]: self._cat_change(row, etype))
            self.cat_table.setCellWidget(r, 1, combo)
            # 说明：该归类当前包含的类型
            types = get_by_category(row["category"])
            self.cat_table.setItem(r, 2, QTableWidgetItem("、".join(types)))
        self.cat_table.setRowHeight(r, 34)

    def _cat_change(self, row: int, etype: str) -> None:
        combo = self.cat_table.cellWidget(row, 1)
        if combo is None:
            return
        new_cat = combo.currentData()
        conn = get_conn()
        try:
            old_cat = conn.execute("SELECT category FROM expense_cat WHERE expense_type=?", (etype,)).fetchone()
            old = old_cat["category"] if old_cat else "其他"
            if old == new_cat:
                return
            set_category(etype, new_cat)
            log_change(conn, "expense_cat", etype, "category", old, new_cat, "费用归类调整")
            conn.commit()
        finally:
            conn.close()
        # 刷新说明列
        for i in range(self.cat_table.rowCount()):
            combo_i = self.cat_table.cellWidget(i, 1)
            if combo_i and combo_i.currentData() == new_cat:
                types = get_by_category(new_cat)
                self.cat_table.setItem(i, 2, QTableWidgetItem("、".join(types)))
        self.log.appendPlainText(f"✓ 费用类型 {etype} 归入「{new_cat}」")

    def _cat_add(self) -> None:
        name = self.cat_new.text().strip()
        if not name:
            QMessageBox.information(self, "提示", "请输入费用类型名称")
            return
        cat = self.cat_new_combo.currentData()
        add_type(name, cat)
        self.cat_new.clear()
        self._load_cat()
        self.log.appendPlainText(f"✓ 已新增费用类型 {name}（归入「{cat}」）")

    # ---- 批量设身份（发票/费用 Tab）----
    def batch_set_type(self) -> None:
        tb = self.tabs.currentWidget()
        if tb not in (self.tab_invoice, self.tab_expense):
            QMessageBox.information(self, "提示", "请在「发票」或「费用」Tab 使用批量设身份")
            return
        rows = tb.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "提示", "请先选中要设置身份的行（可多选）")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("批量设置身份")
        form = QFormLayout(dlg)
        combo = QComboBox()
        for t in ["合伙", "聘用", "兼职", "未标"]:
            combo.addItem(t, userData=t)
        form.addRow("身份", combo)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        ptype = combo.currentData()
        conn = get_conn()
        try:
            n = 0
            for idx in rows:
                r = idx.row()
                if tb is self.tab_expense:
                    eid = self._meta_expense[r]
                    conn.execute("UPDATE expense_ledger SET person_type=? WHERE id=?", (ptype, eid))
                    log_change(conn, "expense_ledger", str(eid), "person_type", None, ptype, "批量设身份")
                else:
                    key = self._meta_invoice[r]
                    conn.execute("UPDATE charge_detail SET person_type=? WHERE invoice_no=?", (ptype, key))
                    log_change(conn, "charge_detail", key, "person_type", None, ptype, "批量设身份")
                n += 1
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback(); QMessageBox.critical(self, "失败", str(e)); return
        finally:
            conn.close()
        self.refresh()
        self.log.appendPlainText(f"✓ 已为 {n} 条数据设置身份「{ptype}」")
        QMessageBox.information(self, "完成", f"已为 {n} 条数据设置身份「{ptype}」")

    # ---- 身份列下拉即时保存 ----
    def _set_invoice_type(self, key: str, combo=None) -> None:
        """发票 Tab 身份下拉：整票统一设置并保存"""
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT person_type FROM charge_detail WHERE invoice_no=? LIMIT 1", (key,)
            ).fetchone()
            new_pt = combo.currentData() if combo is not None else None
            if new_pt is None:
                return
            old_pt = row["person_type"] or "未标" if row else "未标"
            if old_pt == new_pt:
                return
            conn.execute("UPDATE charge_detail SET person_type=? WHERE invoice_no=?", (new_pt, key))
            log_change(conn, "charge_detail", key, "person_type", old_pt, new_pt, "身份列修改")
            conn.commit()
        finally:
            conn.close()
        self.log.appendPlainText(f"✓ 发票 {key[-8:]} 身份 → {new_pt}")

    def _set_expense_type(self, eid: int, combo=None) -> None:
        conn = get_conn()
        try:
            row = conn.execute("SELECT person_type FROM expense_ledger WHERE id=?", (eid,)).fetchone()
            new_pt = combo.currentData() if combo is not None else None
            if new_pt is None:
                return
            old_pt = row["person_type"] or "未标" if row else "未标"
            if old_pt == new_pt:
                return
            conn.execute("UPDATE expense_ledger SET person_type=? WHERE id=?", (new_pt, eid))
            log_change(conn, "expense_ledger", str(eid), "person_type", old_pt, new_pt, "身份列修改")
            conn.commit()
        finally:
            conn.close()
        self.log.appendPlainText(f"✓ 费用 #{eid} 身份 → {new_pt}")
