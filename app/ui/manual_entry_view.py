"""补录（期初/历史应收）发票：待补录发票 / 已补录发票 两页

- 待补录 = 所有「被引用但 invoice 表中没有」的发票号（红字 orig_invoice_no / refund.orig_invoice_no），
  1.1（红字原票缺失）与 1.2（应收对应缺失）合并为同一检测逻辑，列表不区分。
- 统一补录弹窗：原始发票信息 8 项，其中 2.5~2.8（经办人/开票金额/已收金额/收款日期）
  做成可增删的明细表；台账中已有的信息（红字发票的购方/金额/经办人）自动预填。
- 写入 invoice(source='manual') + charge_detail + collection，与现有手工补录完全一致，
  不影响其他页面（收款表/结算/退款判定）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QDateEdit, QHeaderView, QMessageBox, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.ui import scale
from app.ui.widgets import SubtitleLabel, CaptionLabel, PrimaryPushButton, PushButton
from app.ui.column_layout import install_column_layout
from app.engine.backfill_module import (
    list_pending_backfill, list_backfilled, load_invoice_detail,
    save_backfill, delete_backfill,
)


class ManualEntryView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        h = QLabel("补录期初/历史应收的原始发票信息（红字原票缺失或应收对应发票缺失）。"
                   "补录数据与导入数据同模型计算，不影响其他页面。")
        h.setWordWrap(True)
        lay.addWidget(h)

        self.tabs = QTabWidget()
        self.tabs.tabBar().setObjectName("pageTitleBar")
        self.tab_pending = self._make_table(
            ["开票日期", "发票号码", "对方", "价税合计", "状态", "收款金额", "对应红字发票", "操作"], "pending")
        self.tab_done = self._make_table(
            ["开票日期", "发票号码", "对方", "价税合计", "状态", "收款金额", "补录时间", "操作"], "done")
        self.tabs.addTab(self.tab_pending, "待补录发票")
        self.tabs.addTab(self.tab_done, "已补录发票")
        lay.addWidget(self.tabs, 1)

        # 已补录页操作按钮
        done_btns = QHBoxLayout()
        self.btn_new = PrimaryPushButton("新增补录发票")
        self.btn_new.clicked.connect(lambda: self.open_backfill())
        self.btn_edit = PushButton("编辑所选")
        self.btn_edit.clicked.connect(self.edit_selected)
        self.btn_del = PushButton("删除所选")
        self.btn_del.clicked.connect(self.delete_selected)
        done_btns.addWidget(self.btn_new)
        done_btns.addWidget(self.btn_edit)
        done_btns.addWidget(self.btn_del)
        done_btns.addStretch()
        lay.addLayout(done_btns)

        self._pending_meta: dict = {}
        self._done_meta: dict = {}
        self.refresh()

    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def _make_table(self, headers, name: str) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(False)
        t.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        from app.ui.table_features import install_common_features, install_header_filter
        install_common_features(t)
        install_header_filter(t)
        t._col = install_column_layout(t, "manual", name)
        return t

    def refresh(self) -> None:
        self._load_pending()
        self._load_done()

    # ---- 待补录 ----
    def _load_pending(self) -> None:
        rows = list_pending_backfill()
        tbl = self.tab_pending
        tbl.setRowCount(len(rows))
        self._pending_meta = {}
        for r, row in enumerate(rows):
            red_text = "、".join(row["red_invoices"]) if row["red_invoices"] else "—"
            vals = ["—", row["invoice_no"], "—", "—", row["status"],
                    f"{row['collected']:,.2f}", red_text, ""]
            for c, v in enumerate(vals):
                tbl.setItem(r, c, QTableWidgetItem(str(v)))
            btn = PushButton("补录")
            btn.clicked.connect(lambda _checked=False, no=row["invoice_no"]: self.open_backfill(no))
            tbl.setCellWidget(r, 7, btn)
            self._pending_meta[r] = row["invoice_no"]
        tbl._col.apply()

    # ---- 已补录 ----
    def _load_done(self) -> None:
        rows = list_backfilled()
        tbl = self.tab_done
        tbl.setRowCount(len(rows))
        self._done_meta = {}
        for r, row in enumerate(rows):
            vals = [row["invoice_date"], row["invoice_no"], row["buyer"],
                    f"{row['total_amount']:,.2f}", row["status"],
                    f"{row['collected']:,.2f}", row["created_at"], ""]
            for c, v in enumerate(vals):
                tbl.setItem(r, c, QTableWidgetItem(str(v)))
            btn = PushButton("编辑")
            btn.clicked.connect(lambda _checked=False, no=row["invoice_no"]: self.open_backfill(no, edit=True))
            tbl.setCellWidget(r, 7, btn)
            self._done_meta[r] = row["invoice_no"]
        tbl._col.apply()

    # ------------------------------------------------------------------ #
    # 打开补录弹窗（待补录点击 → 预填红字发票信息；已补录编辑 → 预填已存数据）
    # ------------------------------------------------------------------ #
    def open_backfill(self, invoice_no: str | None = None, edit: bool = False) -> None:
        prefill = None
        if invoice_no:
            conn = get_conn()
            try:
                inv = conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (invoice_no,)).fetchone()
            finally:
                conn.close()
            if inv is None:
                prefill = self._prefill_from_red(invoice_no)
            else:
                prefill = load_invoice_detail(invoice_no)
        self._open_dialog(prefill, edit=edit, locked_no=bool(invoice_no and edit))

    def _prefill_from_red(self, orig_no: str) -> dict:
        """待补录（原票缺失）：用引用它的红字发票（来自台账）信息预填。"""
        conn = get_conn()
        try:
            red = conn.execute(
                "SELECT * FROM invoice WHERE orig_invoice_no=? AND total_amount < 0 ORDER BY invoice_date LIMIT 1",
                (orig_no,),
            ).fetchone()
            if red is None:
                return {"invoice_no": orig_no, "handlers": []}
            cds = conn.execute(
                "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=? ORDER BY id",
                (red["invoice_no"],),
            ).fetchall()
        finally:
            conn.close()
        handlers = [
            {"name": cd["person_name"], "billing": abs(cd["billing_amount"]),
             "received": 0.0, "date": ""}
            for cd in cds
        ]
        return {
            "invoice_no": orig_no,
            # 原票尚未入库（正因缺失才补录），拿不到原票开票日期 → 留空让用户手填，
            # 绝不能用红字发票的开票日期顶替。
            "invoice_date": "",
            "buyer": red["buyer"] or "",
            "total_amount": abs(red["total_amount"]),
            "handlers": handlers,
        }

    def _open_dialog(self, prefill: dict | None, edit: bool, locked_no: bool) -> None:
        prefill = prefill or {"invoice_no": "", "invoice_date": "", "buyer": "",
                              "total_amount": 0.0, "handlers": []}
        dlg = QDialog(self)
        dlg.setWindowTitle("编辑补录发票" if edit else "补录原始发票")
        dlg.resize(620, 640)
        lay = QVBoxLayout(dlg)

        form = QFormLayout()
        no_edit = QLineEdit(prefill.get("invoice_no") or "")
        no_edit.setReadOnly(locked_no)
        date_edit = QLineEdit(prefill.get("invoice_date") or "")
        date_edit.setPlaceholderText("YYYY-MM-DD")
        buyer_edit = QLineEdit(prefill.get("buyer") or "")
        amt = QDoubleSpinBox()
        amt.setRange(-99999999, 99999999)
        amt.setDecimals(2)
        amt.setValue(float(prefill.get("total_amount") or 0))
        amt.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)  # 去上下箭头
        form.addRow("发票号码", no_edit)
        form.addRow("开票日期(YYYY-MM-DD)", date_edit)
        form.addRow("对方", buyer_edit)
        form.addRow("价税合计（开票总额）", amt)
        lay.addLayout(form)

        sub = SubtitleLabel("经办人明细（可增删：经办人 / 开票金额 / 已收金额 / 收款日期）")
        lay.addWidget(sub)
        hint = CaptionLabel("台账已有的经办人与开票金额会自动预填；已收金额与收款日期按实际情况填写，留空表示尚未收款。")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        detail = QTableWidget(0, 4)
        detail.setHorizontalHeaderLabels(["经办人", "开票金额", "已收金额", "收款日期"])
        detail.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        detail.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        detail.verticalHeader().setVisible(False)
        from app.ui.table_features import install_common_features, install_header_filter
        install_common_features(detail)
        install_header_filter(detail)
        # 列宽均分填满窗体宽度
        detail.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # 行高固定且与内嵌输入框等高，使输入框四边框正好对齐单元格四框
        detail.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        detail.verticalHeader().setDefaultSectionSize(scale.px(36))
        detail.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # 去掉单元格内边距，让输入框紧贴单元格四边
        detail.setStyleSheet("QTableWidget::item{padding:0px;margin:0px;}")
        lay.addWidget(detail, 1)

        for hd in prefill.get("handlers", []) or []:
            self._add_detail_row(detail, hd.get("name", ""), float(hd.get("billing", 0) or 0),
                                 float(hd.get("received", 0) or 0), hd.get("date", ""))
        if detail.rowCount() == 0:
            self._add_detail_row(detail)

        d_btns = QHBoxLayout()
        b_add = PushButton("增加一行")
        b_add.clicked.connect(lambda: self._add_detail_row(detail))
        b_del = PushButton("删除所选行")
        b_del.clicked.connect(lambda: self._del_detail_row(detail))
        d_btns.addWidget(b_add)
        d_btns.addWidget(b_del)
        d_btns.addStretch()
        lay.addLayout(d_btns)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        no = no_edit.text().strip()
        if not no:
            QMessageBox.warning(dlg, "提示", "发票号码不能为空")
            return
        if not date_edit.text().strip():
            QMessageBox.warning(dlg, "提示", "开票日期(YYYY-MM-DD)为必填项")
            return
        handlers = self._read_detail(detail)
        total = amt.value()
        sum_rec = sum(h["received"] for h in handlers)
        if total > 0 and sum_rec > total + 0.01:
            QMessageBox.warning(dlg, "提示",
                                f"已收金额合计({sum_rec:,.2f})超过价税合计({total:,.2f})")
            return
        data = {
            "invoice_no": no,
            "invoice_date": date_edit.text().strip(),
            "buyer": buyer_edit.text().strip(),
            "total_amount": total,
            "handlers": handlers,
        }
        try:
            save_backfill(data, editing=edit)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.refresh()

    # ---- 经办人明细表 ----
    def _add_detail_row(self, table: QTableWidget, name="", billing=0.0, received=0.0, date="") -> None:
        r = table.rowCount()
        table.insertRow(r)
        ne = QLineEdit(name)
        ne.setPlaceholderText("经办人")
        be = QDoubleSpinBox()
        be.setRange(0, 99999999)
        be.setDecimals(2)
        be.setValue(billing)
        be.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)  # 去上下箭头
        re_ = QDoubleSpinBox()
        re_.setRange(0, 99999999)
        re_.setDecimals(2)
        re_.setValue(received)
        re_.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)  # 去上下箭头
        # 日期：有值才放 QDateEdit；无值（或已收为 0）放空白 QLabel，
        # 绝不显示哨兵日期（旧版曾把 2000-01 当真实日期写入库）。
        has_date = bool(date) and received > 0.001
        if has_date:
            de = QDateEdit()
            de.setCalendarPopup(True)
            de.setDisplayFormat("yyyy-MM")
            d = QDate.fromString(date, "yyyy-MM")
            de.setDate(d if d.isValid() else QDate.currentDate())
            de.setFixedHeight(scale.px(34))
            date_cell = de
        else:
            date_cell = QLabel("")
        # 已收金额 > 0 但日期为空时，自动补出日期框（默认当月）
        re_.valueChanged.connect(lambda v: self._ensure_date_edit(table, r, v))
        # 让输入框四边框对齐单元格：固定高度 + 紧贴四边
        for w in (ne, be, re_):
            w.setFixedHeight(scale.px(34))
        table.setCellWidget(r, 0, ne)
        table.setCellWidget(r, 1, be)
        table.setCellWidget(r, 2, re_)
        table.setCellWidget(r, 3, date_cell)

    def _ensure_date_edit(self, table: QTableWidget, row: int, received: float) -> None:
        """已收金额变为 > 0 时，若收款日期列还是空白占位，则补出日期框（默认当月）。"""
        if received <= 0.001:
            return
        cell = table.cellWidget(row, 3)
        if isinstance(cell, QDateEdit):
            return
        de = QDateEdit()
        de.setCalendarPopup(True)
        de.setDisplayFormat("yyyy-MM")
        de.setDate(QDate.currentDate())
        de.setFixedHeight(scale.px(34))
        table.setCellWidget(row, 3, de)

    def _del_detail_row(self, table: QTableWidget) -> None:
        r = table.currentRow()
        if r >= 0:
            table.removeRow(r)

    def _read_detail(self, table: QTableWidget) -> list:
        handlers = []
        for r in range(table.rowCount()):
            ne = table.cellWidget(r, 0)
            be = table.cellWidget(r, 1)
            re_ = table.cellWidget(r, 2)
            de = table.cellWidget(r, 3)
            if not ne:
                continue
            name = ne.text().strip()
            if not name:
                continue
            handlers.append({
                "name": name,
                "billing": be.value() if be else 0.0,
                "received": re_.value() if re_ else 0.0,
                # 仅 QDateEdit 且日期合法才视为有收款日期，空白占位 → 回空
                "date": de.date().toString("yyyy-MM") if isinstance(de, QDateEdit) and de.date().isValid() else "",
            })
        return handlers

    # ---- 已补录：编辑 / 删除 ----
    def edit_selected(self) -> None:
        row = self.tab_done.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先在「已补录发票」列表中选择一行")
            return
        no = self._done_meta.get(row)
        if not no:
            return
        self.open_backfill(no, edit=True)

    def delete_selected(self) -> None:
        row = self.tab_done.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先在「已补录发票」列表中选择一行")
            return
        no = self._done_meta.get(row)
        if not no:
            return
        if QMessageBox.question(self, "确认删除",
                                f"确定删除补录发票 {no} 吗？\n（若仍被红字发票引用，删除后会重新进入待补录）",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_backfill(no)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "删除失败", str(e))
            return
        self.refresh()
