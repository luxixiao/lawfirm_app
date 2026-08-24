"""发票收款总表"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu

from app.engine.collection import invoice_rows
from app.ui.dialogs import show_red_relation
from app.ui.table_view import BaseTableView, make_filter_widgets


class InvoiceCollectView(BaseTableView):
    def __init__(self) -> None:
        super().__init__(
            "发票收款总表",
            ["开具日期", "发票号码", "购买方名称", "价税合计", "经办人",
             "已收金额", "剩余应收", "备注"],
            "每张发票的收款情况；右击查看红冲信息。",
        )
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._ctx_menu)

    def _total_cols(self) -> set:
        """价税合计(3) / 已收金额(5) / 剩余应收(6)"""
        return {3, 5, 6}

    def _build_filters(self) -> None:
        self.year, self.month, self.src, self.buyer, _ = make_filter_widgets(
            self, self.filters, lambda *_: self.refresh()
        )

    def load_data(self) -> None:
        rows = invoice_rows(
            period=self.month.currentData() or self.year.currentData() or None,
            buyer=self.buyer.text().strip() or None,
            source=self.src.currentData() or None,
        )
        self._rows = [
            [r["invoice_date"], r["invoice_no"], r["buyer"], r["total_amount"],
             r["handlers"], r["collected"], r["remain"], r["remark"]]
            for r in rows
        ]
        self._meta = {i: r for i, r in enumerate(rows)}

    def _green_cols(self) -> set:
        return {5}

    def _ctx_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        meta = self._meta_at(row)
        if not meta:
            return
        menu = QMenu(self)
        act = menu.addAction("查看红冲信息")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == act:
            show_red_relation(self, meta["invoice_no"])
