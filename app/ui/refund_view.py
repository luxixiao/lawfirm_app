"""退款确认：红字发票判定 + 手动确认退款（可多次、可部分）+ 待补录提示

页面分两个子页：
- 待确认：需退款但尚未（完全）确认的红字发票（原票以前月份且已收款）。
- 已确认：已确认退款明细（红字发票日期/号码、原票日期/号码、经办人、退款金额、退款日期）。
"""
from __future__ import annotations

from app.ui.column_layout import install_column_layout
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QMenu, QMessageBox, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.ui.widgets import (CaptionLabel, PrimaryPushButton, PushButton, tab_help_corner)
from app.db import get_conn
from app.engine.refund import evaluate_red_invoices, confirmed_refunds

STATUS_TEXT = {
    "no_orig": "未关联原票",
    "orig_missing": "原票未导入（需补录）",
    "same_month": "原票当月开具，不退款",
    "orig_uncollected": "原票未收款，不退款",
    "need_refund": "需退款",
}

WARN_BG = QColor("#FFF8E6")   # 待补录行底色（淡黄）
WARN_BG_SEL = QColor("#FDF1D1")

PENDING_HEADERS = ["红字发票", "红字金额", "原票号码", "原票日期", "判定", "已退金额", "剩余应退"]
DONE_HEADERS = ["红字发票日期", "红字发票号码", "原票日期", "原票号码", "经办人", "退款金额", "退款日期"]


class RefundView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

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
        btn_view_pending = QPushButton("查看待补录明细 →")
        btn_view_pending.clicked.connect(self.show_pending)
        btns.addWidget(self.btn_add)
        btns.addWidget(btn_view_pending)
        btns.addStretch()
        lay.addLayout(btns)

        # ---- 双子页 ----
        self.tabs = QTabWidget()
        self.tabs.tabBar().setObjectName("pageTitleBar")
        self.tab_pending = QTableWidget(0, len(PENDING_HEADERS))
        self.tab_pending.setHorizontalHeaderLabels(PENDING_HEADERS)
        self._setup_table(self.tab_pending, "pending")
        self.tab_done = QTableWidget(0, len(DONE_HEADERS))
        self.tab_done.setHorizontalHeaderLabels(DONE_HEADERS)
        self._setup_table(self.tab_done, "done")
        # 已确认列表：右击可修改（退款金额 / 退款日期）
        self.tab_done.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_done.customContextMenuRequested.connect(self._on_done_menu)
        self.tabs.addTab(self.tab_pending, "待确认")
        self.tabs.addTab(self.tab_done, "已确认")
        self.tabs.setCornerWidget(tab_help_corner(
            "红字发票退款确认。分为「待确认」（需退款未确认）与「已确认」（已确认退款明细）；"
            "手动填写退款金额与日期，可多次确认（部分退款）。"
        ), Qt.Corner.TopRightCorner)
        lay.addWidget(self.tabs, 1)

        self.refresh()

    @staticmethod
    def _setup_table(table: QTableWidget, key: str) -> None:
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        from app.ui.table_features import install_common_features, install_header_filter
        install_common_features(table)
        install_header_filter(table)
        table._col = install_column_layout(table, "refund", key)

    def showEvent(self, event) -> None:  # noqa: N802
        """切换到本页时自动刷新数据"""
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        # ---------- 待确认 ----------
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
        self._meta = {}
        # 待确认：需退款且仍有剩余应退（含部分退款）；原票未导入走横幅提示
        pend_items = [
            item for item in rows
            if item["status"] == "need_refund"
            and max(abs(item["red_amount"]) - refunded.get(item["red_invoice_no"], 0.0), 0.0) > 0.01
        ]
        self.tab_pending.setRowCount(len(pend_items))
        for r, item in enumerate(pend_items):
            no = item["red_invoice_no"]
            done = refunded.get(no, 0.0)
            refundable = abs(item["red_amount"])
            remain = max(refundable - done, 0.0)
            vals = [no, item["red_amount"], item["orig_invoice_no"] or "—",
                    item["orig_invoice_date"] or "—", STATUS_TEXT.get(item["status"], item["status"]),
                    done, remain]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(
                    "" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                if isinstance(v, float) and v < 0:
                    cell.setForeground(QColor("#C0392B"))
                self.tab_pending.setItem(r, c, cell)
            item["_remain"] = remain
            self._meta[r] = item

        # 待补录横幅（原票未导入）
        missing = [m for m in rows if m["status"] == "orig_missing"]
        if missing:
            self.banner_lbl.setText(f"⚠️ 有 {len(missing)} 张红字发票的原正数发票未导入，"
                                    f"需先手动补录历史数据（补录后自动判定退款）")
            self.banner.show()
        else:
            self.banner.hide()

        self.tab_pending._col.apply()

        # ---------- 已确认 ----------
        done_rows = confirmed_refunds()
        self._done_meta = {}
        self.tab_done.setRowCount(len(done_rows))
        for r, item in enumerate(done_rows):
            self._done_meta[r] = item
            vals = [item["red_invoice_date"] or "—", item["red_invoice_no"],
                    item["orig_invoice_date"] or "—", item["orig_invoice_no"] or "—",
                    item["handlers"] or "—", item["refund_amount"], item["refund_date"]]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(
                    "" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                if c == 5 and isinstance(v, float):  # 退款金额
                    cell.setForeground(QColor("#C0392B"))
                self.tab_done.setItem(r, c, cell)
        self.tab_done._col.apply()

    # ---- 待补录明细 ----
    def show_pending(self) -> None:
        """列出所有待补录（原票未导入）的红字发票，并可跳转手动补录"""
        pending = [m for m in evaluate_red_invoices() if m["status"] == "orig_missing"]
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
        from app.ui.widgets import PrimaryPushButton as PPB
        go_btn = PPB("去补录原票")
        close_btn = PushButton("关闭")
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        btn_row.addWidget(go_btn)
        lay.addLayout(btn_row)

        def go_manual():
            dlg.done(QDialog.DialogCode.Accepted)
            win = self.go_to_page_owner()
            if win and hasattr(win, "go_to_page"):
                win.go_to_page("manual")

        go_btn.clicked.connect(go_manual)
        close_btn.clicked.connect(dlg.reject)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            win = self.go_to_page_owner()
            if win and hasattr(win, "go_to_page"):
                win.go_to_page("manual")

    def go_to_page_owner(self):
        """向上找到主窗口（支持 QDialog 包裹场景）"""
        w = self.window()
        if w is not None and hasattr(w, "go_to_page"):
            return w
        # 兼容被嵌入子 widget 的情况
        p = self.parent()
        while p is not None:
            if hasattr(p, "go_to_page"):
                return p
            p = p.parent()
        return None

    # ---- 确认退款 ----
    def add_refund(self) -> None:
        row = self.tab_pending.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一条红字发票（在「待确认」页）")
            return
        item = self._meta.get(row)
        if item is None:
            return
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

    # ---- 已确认列表：右击修改 ----
    def _on_done_menu(self, pos) -> None:
        row = self.tab_done.currentRow()
        if row < 0:
            return
        if self._done_meta.get(row) is None:
            return
        menu = QMenu(self)
        act = menu.addAction("修改")
        act.triggered.connect(lambda *_: self.edit_refund(row))
        menu.exec(self.tab_done.viewport().mapToGlobal(pos))

    def edit_refund(self, row: int) -> None:
        """修改已确认退款（仅退款金额 / 退款日期；其余字段为发票与拆分表的派生值，只读）。

        保存时写 refund 表并记入修改记录（change_log），快照含发票号/对方/金额/经办人。
        """
        item = self._done_meta.get(row)
        if item is None:
            return
        rid = item["id"]
        no = item["red_invoice_no"]

        dlg = QDialog(self)
        dlg.setWindowTitle(f"修改已确认退款：{no}")
        form = QFormLayout(dlg)

        info = (
            f"红字发票：{item['red_invoice_date'] or '—'} / {no}\n"
            f"原票：{item['orig_invoice_date'] or '—'} / {item['orig_invoice_no'] or '—'}\n"
            f"经办人：{item['handlers'] or '—'}"
        )
        lbl = QLabel(info)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#555; background:#F6F6F4; border:1px solid #E0E0DE;"
                          "border-radius:8px; padding:8px;")
        form.addRow(lbl)

        amt = QDoubleSpinBox()
        amt.setRange(0.01, 99999999)
        amt.setDecimals(2)
        amt.setValue(float(item["refund_amount"] or 0))
        form.addRow("退款金额", amt)

        date = QDateEdit()
        date.setCalendarPopup(True)
        date.setDisplayFormat("yyyy-MM")
        rd = (item["refund_date"] or "")[:7]
        if rd:
            date.setDate(QDate.fromString(rd, "yyyy-MM"))
        else:
            date.setDate(date.date().currentDate())
        form.addRow("退款日期(月)", date)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        amount = round(amt.value(), 2)
        ym = date.date().toString("yyyy-MM")

        conn = get_conn()
        try:
            # 红字发票应退总额（用于上限校验）
            red = conn.execute(
                "SELECT total_amount, buyer FROM invoice WHERE invoice_no=?", (no,)
            ).fetchone()
            red_abs = abs(red["total_amount"]) if red else 0.0
            buyer = red["buyer"] if red else ""
            # 同一红字发票的其它退款（排除本条），校验累计不超应退
            others = conn.execute(
                "SELECT COALESCE(SUM(refund_amount),0) FROM refund "
                "WHERE red_invoice_no=? AND id<>?", (no, rid)
            ).fetchone()[0]
            if others + amount > red_abs + 0.01:
                QMessageBox.warning(self, "提示",
                                    f"累计退款金额（含本次）超过应退金额 {red_abs:,.2f}")
                return
            old = conn.execute(
                "SELECT refund_amount, refund_date FROM refund WHERE id=?", (rid,)
            ).fetchone()
            if abs(float(old["refund_amount"]) - amount) < 0.005 and old["refund_date"] == ym:
                return  # 无变化
            conn.execute(
                "UPDATE refund SET refund_amount=?, refund_date=? WHERE id=?",
                (amount, ym, rid),
            )
            # 写入修改记录（快照：发票号/对方/金额/经办人）
            from app.engine.change_log import log_change
            handlers = item["handlers"] or ""
            for field, ov, nv in (
                ("refund_amount", old["refund_amount"], amount),
                ("refund_date", old["refund_date"], ym),
            ):
                if str(ov) != str(nv):
                    log_change(conn, "refund", str(rid), field, ov, nv,
                               "修改已确认退款",
                               friendly_table="退款",
                               invoice_no=no, buyer=buyer,
                               amount=f"{amount:g}", handlers=handlers)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            QMessageBox.critical(self, "失败", str(e))
            return
        finally:
            conn.close()

        from qfluentwidgets import InfoBar, InfoBarPosition
        InfoBar.success("", "已修改并记入修改记录", parent=self,
                        position=InfoBarPosition.TOP_RIGHT, duration=2500)
        self.refresh()
