"""发票收款总表"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu

from app.engine.collection import invoice_rows
from app.ui.dialogs import show_red_relation
from app.ui.table_view import BaseTableView, make_filter_widgets, RED, build_period


class InvoiceCollectView(BaseTableView):
    def __init__(self) -> None:
        super().__init__(
            "发票收款情况",
            ["开具日期", "发票号码", "购买方名称", "价税合计", "经办人",
             "已收金额", "剩余应收", "收退款情况"],
            "每张发票的收款/退款/红冲情况（收款月+金额、红冲月+金额、退款月+金额合并于一列）；左键点表头排序，右键点表头按列筛选；搜索框可搜发票号码/购方名/金额/经办人；右击查看红冲信息。当月开具并当月红冲的发票不显示收款。",
            page_key="invoice",
        )
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._ctx_menu)
        self._enable_sort_and_filter()

    def _total_cols(self) -> set:
        """价税合计(3) / 已收金额(5) / 剩余应收(6)"""
        return {3, 5, 6}

    def _build_filters(self) -> None:
        self.year, self.month, self.src, self.search, _ = make_filter_widgets(
            self, self.filters, lambda *_: self.refresh(),
            search_label="发票号码/购方名/金额/经办人"
        )

    def load_data(self) -> None:
        rows = invoice_rows(
            period=build_period(self.year.currentData(), self.month.currentData()),
            keyword=self.search.text().strip() or None,
            source=self.src.currentData() or None,
        )
        self._rows = [
            [r["invoice_date"], r["invoice_no"], r["buyer"], r["total_amount"],
             r["handlers_amount"], r["collected"], r["remain"], r["recv_refund_str"]]
            for r in rows
        ]
        self._meta = {i: r for i, r in enumerate(rows)}

    def _green_cols(self) -> set:
        return {5}

    def _make_item(self, val, r: int, c: int):
        item = super()._make_item(val, r, c)
        # 已确认退款的红字发票：收款日期列标红并显示退款月
        if c == 7:
            meta = self._meta.get(r)
            if meta and meta.get("is_refunded"):
                item.setForeground(RED)
        return item

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
