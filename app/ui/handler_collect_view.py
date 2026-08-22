"""经办人发票收款总表（一票多经办人拆行）"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel, QMenu

from app.db import get_conn
from app.engine.collection import handler_rows
from app.ui.dialogs import show_invoice_handlers, show_red_relation
from app.ui.table_view import BaseTableView, make_filter_widgets


class HandlerCollectView(BaseTableView):
    def __init__(self) -> None:
        super().__init__(
            "经办人发票收款总表",
            ["开具日期", "发票号码", "购买方名称", "开票总额", "经办人",
             "身份", "开票金额", "已收金额", "剩余应收", "备注"],
            "经办人维度收款情况；身份列可直接修改经办人身份；右击查看红冲信息 / 其他经办人金额。",
        )
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._ctx_menu)

    def _total_cols(self) -> set:
        """开票总额(3) / 开票金额(6) / 已收金额(7) / 剩余应收(8)"""
        return {3, 6, 7, 8}

    def _build_filters(self) -> None:
        self.month, self.src, self.buyer, _ = make_filter_widgets(
            self, self.filters, lambda *_: self.refresh()
        )
        # 经办人下拉
        lbl = QLabel("经办人")
        self.person = QComboBox()
        self.person.addItem("全部经办人", userData="")
        conn = get_conn()
        try:
            for r in conn.execute("SELECT DISTINCT person_name FROM charge_detail ORDER BY person_name"):
                self.person.addItem(r["person_name"], userData=r["person_name"])
        finally:
            conn.close()
        self.person.currentIndexChanged.connect(lambda *_: self.refresh())
        self.filters.insertWidget(0, lbl)
        self.filters.insertWidget(1, self.person)

    def load_data(self) -> None:
        rows = handler_rows(
            person=self.person.currentData() or None,
            period=self.month.currentData() or None,
            buyer=self.buyer.text().strip() or None,
            source=self.src.currentData() or None,
        )
        self._rows = [
            [r["invoice_date"], r["invoice_no"], r["buyer"], r["total_amount"],
             r["person_name"], r["person_type"] or "未标",
             r["billing_amount"], r["collected"], r["remain"], r["receipt_dates"]]
            for r in rows
        ]
        self._meta = {i: r for i, r in enumerate(rows)}

    def _green_cols(self) -> set:
        return {6}

    def _render(self) -> None:
        super()._render()
        # 身份列（索引5）：每行=一个经办人，下拉直接修改（精确到发票+经办人）
        from PySide6.QtWidgets import QComboBox
        for r, row in enumerate(self._rows):
            combo = QComboBox()
            for t in ["未标", "合伙", "聘用", "兼职"]:
                combo.addItem(t, userData=t)
            combo.setCurrentText(row[5] or "未标")
            no, name = row[1], row[4]
            combo.currentIndexChanged.connect(
                lambda *_, no=no, nm=name, c=combo: self._set_handler_type(no, nm, c))
            self.table.setCellWidget(r, 5, combo)

    def _set_handler_type(self, invoice_no: str, person_name: str, combo) -> None:
        """按 发票+经办人 精确设置身份"""
        from app.db import get_conn
        from app.engine.change_log import log_change
        new_pt = combo.currentData()
        if new_pt is None:
            return
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT person_type FROM charge_detail WHERE invoice_no=? AND person_name=? LIMIT 1",
                (invoice_no, person_name)).fetchone()
            old_pt = row["person_type"] or "未标" if row else "未标"
            if old_pt == new_pt:
                return
            conn.execute(
                "UPDATE charge_detail SET person_type=? WHERE invoice_no=? AND person_name=?",
                (new_pt, invoice_no, person_name))
            log_change(conn, "charge_detail", f"{invoice_no}/{person_name}", "person_type",
                       old_pt, new_pt, "经办人总表身份修改")
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
        finally:
            conn.close()

    def _ctx_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        meta = self._meta_at(row)
        if not meta:
            return
        menu = QMenu(self)
        a1 = menu.addAction("查看红冲信息")
        a2 = menu.addAction("查看其他经办人金额")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == a1:
            show_red_relation(self, meta["invoice_no"])
        elif chosen == a2:
            show_invoice_handlers(self, meta["invoice_no"], meta["person_name"])
