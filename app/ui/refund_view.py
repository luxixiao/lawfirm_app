"""退款确认：红字发票判定 + 手动确认退款（可多次、可部分）+ 待补录提示"""
from __future__ import annotations

from app.ui.column_state import attach_persistence, restore_col_widths
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.ui.widgets import (SubtitleLabel, CaptionLabel, PrimaryPushButton, PushButton)
from app.db import get_conn
from app.engine.refund import evaluate_red_invoices

STATUS_TEXT = {
    "no_orig": "未关联原票",
    "orig_missing": "原票未导入（需补录）",
    "same_month": "原票当月开具，不退款",
    "orig_uncollected": "原票未收款，不退款",
    "need_refund": "需退款",
}

WARN_BG = QColor("#FFF8E6")   # 待补录行底色（淡黄）
WARN_BG_SEL = QColor("#FDF1D1")


class RefundView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        t = SubtitleLabel("退款")
        lay.addWidget(t)
        h = QLabel("红字发票退款确认。手动填写退款金额与日期，可多次确认（部分退款）。")
        lay.addWidget(h)

        # ---- 待补录警告横幅 ----
        self.banner = QWidget()
        self.banner.setStyleSheet(
            "QWidget#banner { background:#FFF8E6; border:1px solid #F0DFA8; border-radius:8px; }"
            "QLabel { color:#7A5C00; font-weight:600; }"
            "QPushButton { background:#7A5C00; color:white; border:none; border-radius:6px; padding:5px 14px; }"
            "QPushButton:hover { background:#8F6D00; }"
        )
        self.banner.setObjectName("banner")
        b_lay = QHBoxLayout(self.banner)
        b_lay.setContentsMargins(12, 8, 12, 8)
        self.banner_lbl = QLabel("")
        self.btn_pending = QPushButton("查看待补录明细 →")
        self.btn_pending.clicked.connect(self.show_pending)
        b_lay.addWidget(self.banner_lbl)
        b_lay.addStretch()
        b_lay.addWidget(self.btn_pending)
        self.banner.hide()
        lay.addWidget(self.banner)

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
        attach_persistence(self.table, "refund", "main")

        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        """切换到本页时自动刷新数据"""
        super().showEvent(event)
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
        pending = 0
        for r, item in enumerate(rows):
            no = item["red_invoice_no"]
            done = refunded.get(no, 0.0)
            refundable = abs(item["red_amount"])
            # 剩余应退：仅"需退款"状态计算；不退款/未判定显示 "—"（不产生应退义务）
            if item["status"] == "need_refund":
                remain = max(refundable - done, 0.0)
                remain_display = f"{remain:,.2f}"
            else:
                remain = 0.0
                remain_display = "—"
            vals = [no, item["red_amount"], item["orig_invoice_no"] or "—",
                    item["orig_invoice_date"] or "—", STATUS_TEXT.get(item["status"], item["status"]),
                    done, remain_display]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(
                    "" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                if isinstance(v, float) and v < 0:
                    cell.setForeground(QColor("#C0392B"))
                if item["status"] == "orig_missing":
                    cell.setBackground(WARN_BG)
                    if c == 4:
                        cell.setForeground(QColor("#7A5C00"))
                self.table.setItem(r, c, cell)
            item["_remain"] = remain
            self._meta[r] = item
            if item["status"] == "orig_missing":
                pending += 1

        # 待补录横幅
        if pending:
            self.banner_lbl.setText(f"⚠️ 有 {pending} 张红字发票的原正数发票未导入，"
                                    f"需先手动补录历史数据（补录后自动判定退款）")
            self.banner.show()
        else:
            self.banner.hide()

        if restore_col_widths(self.table, "refund", "main"):
            self.table.horizontalHeader().setStretchLastSection(False)

    # ---- 待补录明细 ----
    def show_pending(self) -> None:
        """列出所有待补录（原票未导入）的红字发票，并可跳转手动补录"""
        pending = [m for m in self._meta.values() if m["status"] == "orig_missing"]
        if not pending:
            QMessageBox.information(self, "提示", "没有待补录的红字发票")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("待补录历史数据")
        dlg.resize(720, 380)
        lay = QVBoxLayout(dlg)
        tip = QLabel("以下红字发票的原正数发票不在已导入数据中（跨年/无期初文档）。\n"
                     "请在「手动补录」中添加原正数发票（及收款情况），补录后回到本页自动更新判定。")
        tip.setStyleSheet("color:#7A5C00; background:#FFF8E6; border:1px solid #F0DFA8;"
                          "border-radius:8px; padding:10px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        tbl = QTableWidget(len(pending), 5)
        tbl.setHorizontalHeaderLabels(["红字发票", "红字金额", "原票号码", "应退金额", "状态"])
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setStretchLastSection(True)
        for r, m in enumerate(pending):
            vals = [m["red_invoice_no"], m["red_amount"], m["orig_invoice_no"] or "—",
                    m["refund_amount"], STATUS_TEXT["orig_missing"]]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem("" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                if c == 2:
                    cell.setForeground(QColor("#C0392B"))  # 缺失的原票号标红
                tbl.setItem(r, c, cell)
        lay.addWidget(tbl)
        # 按钮（用普通 PushButton + clicked 信号，不用 QDialogButtonBox 自动映射）
        btn_row = QHBoxLayout()
        from app.ui.widgets import PrimaryPushButton, PushButton
        go_btn = PrimaryPushButton("去补录原票")
        close_btn = PushButton("关闭")
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        btn_row.addWidget(go_btn)
        lay.addLayout(btn_row)

        def go_manual():
            dlg.done(QDialog.DialogCode.Accepted)
            win = self.window()
            if hasattr(win, "go_to_page"):
                win.go_to_page("manual")

        go_btn.clicked.connect(go_manual)
        close_btn.clicked.connect(dlg.reject)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            win = self.window()
            if hasattr(win, "go_to_page"):
                win.go_to_page("manual")

    # ---- 确认退款 ----
    def add_refund(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一条红字发票")
            return
        item = self._meta[row]
        no = item["red_invoice_no"]
        # 仅"需退款"状态可确认退款；其他状态（不退款/未判定）禁止
        if item["status"] != "need_refund":
            tip = {
                "same_month": "原票当月开具，系统判定不退款",
                "orig_uncollected": "原票未收款，系统判定不退款",
                "orig_missing": "原票未导入，请先补录原票后再判定",
                "no_orig": "未关联原票，无法判定",
            }.get(item["status"], "该发票不满足退款条件")
            QMessageBox.information(self, "无法确认退款", f"发票 {no}：{tip}")
            return

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
