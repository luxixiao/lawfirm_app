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
             "开票金额", "已收金额", "剩余应收", "备注"],
            "经办人维度收款情况；右击查看红冲信息 / 其他经办人金额。",
        )
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._ctx_menu)

    def _total_cols(self) -> set:
        """开票总额(3) / 开票金额(5) / 已收金额(6) / 剩余应收(7)"""
        return {3, 5, 6, 7}

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
             r["person_name"], r["billing_amount"], r["collected"], r["remain"],
             r["receipt_dates"]]
            for r in rows
        ]
        self._meta = {i: r for i, r in enumerate(rows)}

    def _green_cols(self) -> set:
        return {6}

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
