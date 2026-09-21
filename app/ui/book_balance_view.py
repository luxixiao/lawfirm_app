"""账面情况页：按账期（年/月区间）汇总各「会计科目」的账面费用金额（book_amount）。

设计口径（对齐已确认决策）：
- 数据源与聚合：见 app.engine.book_balance.load_pivot（纯逻辑，无头可测）。
- 展示：行 = 会计科目（一级分组、二级缩进、顺序 = 「会计科目」页 sort_order）；
  列 = 选定月份的逐月列 + 末尾「总计」列；每个一级科目有加粗小计行，底部有「合计」行。
- 默认年份 = 台账中最新年份；默认区间 = 当年 1~12 月；可用「起始月/终止月」缩到区间（如 2025-01~2025-02）。
- 主表「会计科目」未配置、但台账里出现的科目（旧数据/孤儿科目）也如实展示，置于末尾、按名称排序，
  保证「账面情况」不漏数据。
- 只读：表不可编辑，仅用于查看与核对。
- 下钻（T3）：双击单元 = 弹只读 5 列小表（Qt.Popup）；右键单元「查看详情」= 整页跳转到
  组成该单元的全部费用台账行（ExpenseDetailView，内部 QStackedWidget 承载，不在 main_window 注册新页）。
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QMenu,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.engine import book_balance as bb
from app.ui import scale
from app.ui.column_layout import install_column_layout
from app.ui.expense_detail_view import ExpenseDetailView
from app.ui.widgets import CaptionLabel, ComboBox, DialogTitleLabel, PageHeader, PushButton, TableWidget


def _fmt(x: float) -> str:
    return f"{x:,.2f}"


class _PivotLoadThread(QThread):
    """后台加载账面情况透视数据，避免点开页面时 UI 卡顿（load_pivot 含全表 GROUP BY）。"""

    resultReady = Signal(dict)

    def __init__(self, year: str, start: int, end: int) -> None:
        super().__init__()
        self._year = year
        self._start = start
        self._end = end

    def run(self) -> None:  # noqa: N802
        res = bb.load_pivot(year=self._year, start=self._start, end=self._end)
        self.resultReady.emit(res)


class _PivotPage(QWidget):
    """账面情况透视页（T3：被 BookBalanceView 内部 QStackedWidget 承载的 page0）。

    持有年份/月份/刷新工具条 + 透视表 + 底部提示，并负责把「双击/右键单元」转成下钻信号：
    - drillPopupRequested(s1, s2, kind) → 双击 → 父级弹只读 5 列小表；
    - drillDetailRequested(s1, s2, kind) → 右键「查看详情」→ 父级整页跳转详情。
    """

    drillPopupRequested = Signal(str, str, str)   # s1, s2, kind
    drillDetailRequested = Signal(str, str, str)  # s1, s2, kind

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        lay.addWidget(PageHeader(
            "账面情况",
            "按账期汇总各「会计科目」的账面费用金额（账面费用金额 = 费用金额 - 税额）。"
            "行顺序与「数据维护 → 会计科目」一致；未配置的旧科目也会如实列出。只读，用于核对。"
            "双击单元或右键「查看详情」可下钻到组成该单元的费用台账行。",
        ))

        # 工具栏：年份 / 起始月 / 终止月 / 刷新
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("年份"))
        self.year_combo = ComboBox()
        self.year_combo.setMinimumWidth(scale.px(90))
        self.year_combo.currentIndexChanged.connect(lambda _: self._rebuild())
        bar.addWidget(self.year_combo)

        bar.addWidget(QLabel("起始月"))
        self.start_combo = ComboBox()
        for m in range(1, 13):
            self.start_combo.addItem(f"{m}月", m)
        self.start_combo.setCurrentIndex(0)
        self.start_combo.setMinimumWidth(scale.px(72))
        self.start_combo.currentIndexChanged.connect(lambda _: self._rebuild())
        bar.addWidget(self.start_combo)

        bar.addWidget(QLabel("终止月"))
        self.end_combo = ComboBox()
        for m in range(1, 13):
            self.end_combo.addItem(f"{m}月", m)
        self.end_combo.setCurrentIndex(11)
        self.end_combo.setMinimumWidth(scale.px(72))
        self.end_combo.currentIndexChanged.connect(lambda _: self._rebuild())
        bar.addWidget(self.end_combo)

        btn_refresh = PushButton("刷新")
        btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(btn_refresh)
        bar.addStretch(1)
        lay.addLayout(bar)

        # 表格（统一列组件：右键表头「列设置…」可显隐/重排/定宽/冻结）
        self.table = TableWidget(self)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.cellDoubleClicked.connect(self._on_cell_double)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self._col = install_column_layout(self.table, "book_balance", "main")
        lay.addWidget(self.table, 1)

        # 底部提示
        self.hint = CaptionLabel("")
        lay.addWidget(self.hint)

        # 下钻所需的当前选择（双击/右键时取用）
        self._year = str(datetime.now().year)
        self._months: List[int] = list(range(1, 13))
        self._row_specs: List[dict] = []
        self._req = 0          # 异步加载请求令牌（仅最新一次渲染）
        self._loader = None
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
        """异步加载：先返回（页面立即显示），数据就绪后由 _on_loaded 填充。"""
        year = self.year_combo.currentData() or str(datetime.now().year)
        start = int(self.start_combo.currentData())
        end = int(self.end_combo.currentData())
        if start > end:
            start, end = end, start
        self._year = year
        self._months = list(range(start, end + 1))
        self._req += 1
        token = self._req
        self.hint.setText("加载中…")
        th = _PivotLoadThread(year, start, end)
        th.resultReady.connect(lambda res: self._on_loaded(res, token))
        th.finished.connect(th.deleteLater)
        self._loader = th
        th.start()

    def _on_loaded(self, res: dict, token: int) -> None:
        if token != self._req:
            return  # 已有更新的请求，丢弃过期结果
        self._render(res["rows"], res["months"], res["year"], res["start"], res["end"])
        self._col.apply()

    def _render(self, row_specs: List[dict], months, year: str, start: int, end: int) -> None:
        self._row_specs = row_specs
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

        # 列宽/显隐/重排交给统一列组件（install_column_layout + _col.apply）

        n_groups = sum(1 for s in row_specs if s["bold"] and not s.get("grand"))
        if row_specs:
            total_all = sum(s["total"] for s in row_specs if s["bold"] and not s.get("grand"))
            self.hint.setText(
                f"显示 {year}年 {start}月 ~ {end}月：共 {n_groups} 个一级科目，"
                f"账面费用金额合计 {_fmt(total_all)}")
        else:
            self.hint.setText(f"（{year}年 {start}月 ~ {end}月 区间内暂无费用台账数据）")

    # -- 下钻入口 -------------------------------------------------------
    def _spec_at(self, row: int) -> dict | None:
        if 0 <= row < len(self._row_specs):
            return self._row_specs[row]
        return None

    def _on_cell_double(self, row: int, _col: int) -> None:
        spec = self._spec_at(row)
        if spec is None:
            return
        self.drillPopupRequested.emit(spec["s1"], spec["s2"], spec["kind"])

    def _on_context_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        spec = self._spec_at(row)
        if spec is None:
            return
        menu = QMenu(self)
        act = menu.addAction("查看详情")
        s1, s2, kind = spec["s1"], spec["s2"], spec["kind"]
        act.triggered.connect(
            lambda _checked=False, s1=s1, s2=s2, kind=kind:
            self.drillDetailRequested.emit(s1, s2, kind))
        menu.exec(self.table.mapToGlobal(pos))

    # -- 当前选择（供父级下钻取用） -------------------------------------
    def current_year(self) -> str:
        return self._year

    def current_months(self) -> List[int]:
        return list(self._months)

    def refresh(self) -> None:
        self._refresh_years()
        self._rebuild()


class _DrillPopup(QDialog):
    """双击单元弹出的只读 5 列小表（Qt.Popup：点空白自动关闭，内部按钮可用）。

    5 列 = 名称 / 费用金额 / 税额 / 账面费用金额 / 费用类型（逐行展示组成该单元的全部行）。
    底部「查看详情」→ 整页跳转到 ExpenseDetailView。
    """

    _COLS = [("名称", "name", False), ("费用金额", "expense_amount", True),
             ("税额", "tax_amount", True), ("账面费用金额", "book_amount", True),
             ("费用类型", "expense_type", False)]

    def __init__(self, s1: str, s2: str, kind: str, months, year,
                 on_detail, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("drillPopup")
        rows = bb.expense_rows_for_cell(str(year), months, s1, s2, kind, get_conn())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(scale.px(14), scale.px(12), scale.px(14), scale.px(12))
        lay.setSpacing(scale.px(8))

        title = DialogTitleLabel(self._title(kind, s1, s2, year, rows))
        lay.addWidget(title)

        if not rows:
            lay.addWidget(CaptionLabel("（该单元暂无明细行）"))
        else:
            table = QTableWidget(len(rows), len(self._COLS))
            table.setHorizontalHeaderLabels([c[0] for c in self._COLS])
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            table.verticalHeader().setVisible(False)
            table.setWordWrap(False)
            for r, row in enumerate(rows):
                for c, (_hdr, key, money) in enumerate(self._COLS):
                    v = row.get(key)
                    if money and isinstance(v, (int, float)) and not isinstance(v, bool):
                        txt = f"{v:,.2f}"
                    else:
                        txt = "" if v is None else str(v)
                    it = QTableWidgetItem(txt)
                    it.setTextAlignment(
                        Qt.AlignRight | Qt.AlignVCenter if money
                        else Qt.AlignLeft | Qt.AlignVCenter)
                    table.setItem(r, c, it)
            # 自适应：列/行按内容收紧并关掉滚动条，让弹窗正好包住内容
            table.horizontalHeader().setStretchLastSection(False)
            table.resizeColumnsToContents()
            table.resizeRowsToContents()
            table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            # 表格锁定为内容尺寸（无内部滚动条）；内容过多时限制高度并退回纵向滚动
            cw = table.verticalHeader().width() + table.horizontalHeader().length()
            ch = table.horizontalHeader().height() + table.verticalHeader().length()
            fw = table.frameWidth()
            content = (cw + 2 * fw, ch + 2 * fw)
            max_h = scale.px(520)
            if self.screen() is not None:
                max_h = int(self.screen().availableGeometry().height() * 0.7)
            if content[1] <= max_h:
                table.setFixedSize(*content)
            else:
                table.setFixedWidth(content[0])
                table.setFixedHeight(max_h)
                table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            lay.addWidget(table)

        btn = PushButton("查看详情")
        btn.setFixedHeight(scale.px(34))
        btn.clicked.connect(
            lambda _checked=False: (on_detail(s1, s2, kind, months, year), self.close()))
        lay.addWidget(btn)
        self.adjustSize()

    @staticmethod
    def _title(kind: str, s1: str, s2: str, year, rows: list) -> str:
        if kind == "grand":
            label = "全部费用"
        elif kind == "uncat":
            label = f"{s1}（未分类）"
        elif kind == "l2":
            label = f"{s1} / {s2}"
        else:  # l1
            label = s1 or "全部费用"
        return f"{year} {label}：{len(rows)} 行明细"


class BookBalanceView(QWidget):
    """账面情况页容器：内部 QStackedWidget 在「透视页」与「费用台账详情页」之间切换。

    - page0 = _PivotPage（只读透视表 + 下钻入口）
    - page1 = ExpenseDetailView（下钻详情，T3 只读骨架；编辑留 T4）
    详情页占满本页内容区（等效整页跳转），但**不**在 main_window 导航注册新页。
    """

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._stack = QStackedWidget()
        self._pivot = _PivotPage()
        self._detail = ExpenseDetailView(on_back=self._back_to_pivot)
        self._stack.addWidget(self._pivot)
        self._stack.addWidget(self._detail)
        lay.addWidget(self._stack, 1)

        self._pivot.drillPopupRequested.connect(self._show_drill_popup)
        self._pivot.drillDetailRequested.connect(self._route_detail)

    # -- 下钻路由 -------------------------------------------------------
    def _back_to_pivot(self) -> None:
        self._pivot.refresh()
        self._stack.setCurrentWidget(self._pivot)

    def _route_detail(self, s1: str, s2: str, kind: str) -> None:
        self._open_detail(s1, s2, kind,
                          self._pivot.current_months(), self._pivot.current_year())

    def _show_drill_popup(self, s1: str, s2: str, kind: str) -> None:
        popup = _DrillPopup(s1, s2, kind,
                            self._pivot.current_months(), self._pivot.current_year(),
                            self._open_detail, self)
        popup.move(QCursor.pos())
        popup.exec()

    def _open_detail(self, s1: str, s2: str, kind: str, months, year) -> None:
        self._detail.load(s1, s2, kind, months, year)
        self._stack.setCurrentWidget(self._detail)

    def refresh(self) -> None:
        self._pivot.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._pivot.refresh()
