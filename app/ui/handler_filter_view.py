"""经办人发票收款表（单经办人筛选）"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel, QMenu

from app.db import get_conn
from app.engine.collection import handler_rows
from app.ui.dialogs import show_invoice_handlers, show_red_relation
from app.ui.table_view import BaseTableView


class HandlerFilterView(BaseTableView):
    def __init__(self) -> None:
        super().__init__(
            "经办人发票收款表",
            ["开具日期", "发票号码", "购买方名称", "开票总额", "经办人",
             "开票金额", "已收金额", "剩余应收", "备注"],
            "选择经办人后，仅列出该经办人的发票。",
        )
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._ctx_menu)

    def _build_filters(self) -> None:
        self.filters.addWidget(QLabel("经办人"))
        self.person = QComboBox()
        self.person.setMinimumWidth(140)
        self.person.addItem("请选择经办人", None)
        conn = get_conn()
        try:
            for r in conn.execute("SELECT DISTINCT person_name FROM charge_detail ORDER BY person_name"):
                self.person.addItem(r["person_name"], r["person_name"])
        finally:
            conn.close()
        self.person.currentIndexChanged.connect(lambda *_: self.refresh())
        self.filters.addWidget(self.person)
        self.filters.addWidget(QLabel("开票月份"))
        self.month = QComboBox()
        self.month.addItem("全部月份", "")
        from app.ui.table_view import _month_options
        for m in _month_options():
            self.month.addItem(m, m)
        self.month.currentIndexChanged.connect(lambda *_: self.refresh())
        self.filters.addWidget(self.month)
        self.filters.addStretch()

    def load_data(self) -> None:
        person = self.person.currentData()
        if not person:
            self._rows = []
            self._meta = {}
            return
        rows = handler_rows(
            person=person,
            period=self.month.currentData() or None,
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
