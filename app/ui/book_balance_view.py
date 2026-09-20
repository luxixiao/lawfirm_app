"""账面情况页：按账期（年/月区间）汇总各「会计科目」的账面费用金额（book_amount）。

设计口径（对齐已确认决策）：
- 数据源与聚合：见 app.engine.book_balance.load_pivot（纯逻辑，无头可测）。
- 展示：行 = 会计科目（一级分组、二级缩进、顺序 = 「会计科目」页 sort_order）；
  列 = 选定月份的逐月列 + 末尾「总计」列；每个一级科目有加粗小计行，底部有「合计」行。
- 默认年份 = 台账中最新年份；默认区间 = 当年 1~12 月；可用「起始月/终止月」缩到区间（如 2025-01~2025-02）。
- 主表「会计科目」未配置、但台账里出现的科目（旧数据/孤儿科目）也如实展示，置于末尾、按名称排序，
  保证「账面情况」不漏数据。
- 只读：表不可编辑，仅用于查看与核对。
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.engine import book_balance as bb
from app.ui.widgets import CaptionLabel, ComboBox, PageHeader, PushButton, TableWidget


def _fmt(x: float) -> str:
    return f"{x:,.2f}"


class BookBalanceView(QWidget):
    def __init__(self) -> None:
        super().__init__()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        lay.addWidget(PageHeader(
            "账面情况",
            "按账期汇总各「会计科目」的账面费用金额（账面费用金额 = 费用金额 - 税额）。"
            "行顺序与「数据维护 → 会计科目」一致；未配置的旧科目也会如实列出。只读，用于核对。",
        ))

        # 工具栏：年份 / 起始月 / 终止月 / 刷新
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("年份"))
        self.year_combo = ComboBox()
        self.year_combo.setMinimumWidth(90)
        self.year_combo.currentIndexChanged.connect(lambda _: self._rebuild())
        bar.addWidget(self.year_combo)

        bar.addWidget(QLabel("起始月"))
        self.start_combo = ComboBox()
        for m in range(1, 13):
            self.start_combo.addItem(f"{m}月", m)
        self.start_combo.setCurrentIndex(0)
        self.start_combo.setMinimumWidth(72)
        self.start_combo.currentIndexChanged.connect(lambda _: self._rebuild())
        bar.addWidget(self.start_combo)

        bar.addWidget(QLabel("终止月"))
        self.end_combo = ComboBox()
        for m in range(1, 13):
            self.end_combo.addItem(f"{m}月", m)
        self.end_combo.setCurrentIndex(11)
        self.end_combo.setMinimumWidth(72)
        self.end_combo.currentIndexChanged.connect(lambda _: self._rebuild())
        bar.addWidget(self.end_combo)

        btn_refresh = PushButton("刷新")
        btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(btn_refresh)
        bar.addStretch(1)
        lay.addLayout(bar)

        # 表格
        self.table = TableWidget(self)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)

        # 底部提示
        self.hint = CaptionLabel("")
        lay.addWidget(self.hint)

        self._refresh_years()
        self._rebuild()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_years()
        self._rebuild()

    # -- 年份下拉（默认最新） -------------------------------------------
    def _refresh_years(self) -> None:
        conn = get_conn()
        try:
            yrs = [r[0] for r in conn.execute(
                "SELECT DISTINCT substr(period,1,4) AS y FROM expense_ledger "
                "WHERE period LIKE '____-__' ORDER BY y DESC")]
        finally:
            conn.close()
        if not yrs:
            yrs = [str(datetime.now().year)]

        prev = self.year_combo.currentData()
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        for y in yrs:
            self.year_combo.addItem(f"{y}年", y)
        idx = self.year_combo.findData(prev) if prev else -1
        self.year_combo.setCurrentIndex(0 if idx < 0 else idx)
        self.year_combo.blockSignals(False)

    # -- 渲染 -----------------------------------------------------------
    def _rebuild(self) -> None:
        year = self.year_combo.currentData() or str(datetime.now().year)
        start = int(self.start_combo.currentData())
        end = int(self.end_combo.currentData())
        if start > end:
            start, end = end, start
        res = bb.load_pivot(year=year, start=start, end=end)
        self._render(res["rows"], res["months"], year, start, end)

    def _render(self, row_specs: List[dict], months, year: str, start: int, end: int) -> None:
        headers = ["科目"] + [f"{m}月" for m in months] + ["总计"]
        self.table.setColumnCount(len(headers))
        self.table.setRowCount(len(row_specs))
        self.table.setHorizontalHeaderLabels(headers)

        bold = self.font()
        bold.setBold(True)

        for ri, spec in enumerate(row_specs):
            name_item = QTableWidgetItem(spec["name"])
            if spec["bold"]:
                name_item.setFont(bold)
            if spec.get("grand"):
                name_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(ri, 0, name_item)
            for ci, v in enumerate(spec["vals"]):
                it = QTableWidgetItem(_fmt(v))
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if spec["bold"]:
                    it.setFont(bold)
                self.table.setItem(ri, 1 + ci, it)
            tot_it = QTableWidgetItem(_fmt(spec["total"]))
            tot_it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if spec["bold"]:
                tot_it.setFont(bold)
            self.table.setItem(ri, len(headers) - 1, tot_it)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(headers)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.resizeColumnsToContents()

        n_groups = sum(1 for s in row_specs if s["bold"] and not s.get("grand"))
        if row_specs:
            total_all = sum(s["total"] for s in row_specs if s["bold"] and not s.get("grand"))
            self.hint.setText(
                f"显示 {year}年 {start}月 ~ {end}月：共 {n_groups} 个一级科目，"
                f"账面费用金额合计 {_fmt(total_all)}")
        else:
            self.hint.setText(f"（{year}年 {start}月 ~ {end}月 区间内暂无费用台账数据）")

    def refresh(self) -> None:
        self._refresh_years()
        self._rebuild()
