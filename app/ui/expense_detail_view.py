"""账面情况 → 费用台账详情页（T3 只读骨架；编辑/保存留 T4）。

整页跳转（非内嵌面板、非抽屉）：由 BookBalanceView 内部 QStackedWidget 承载，
占满账面情况内容区。本页只读展示组成某透视单元格的全部 expense_ledger 行，
并提供「← 返回账面情况」与「查看修改记录」（内嵌统一审计中心 AuditView）。

皮肤契约（硬性）：
- 颜色一律经 style.qcolor（如 text_mute / border / accent_blue_bg），禁止十六进制字面量与 QColor(r,g,b)；
- 固定尺寸一律 scale.px(基准值)，禁止裸数字。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.engine import book_balance as bb
from app.ui import scale, style
from app.ui.audit_view import AuditView
from app.ui.expense_ledger_view import _HEADERS as _LEDGER_HEADERS
from app.ui.expense_ledger_view import _KEYS as _LEDGER_KEYS
from app.ui.widgets import CaptionLabel, DialogTitleLabel

# 复用费用台账页的列定义（顺序 = 源文件列序），尾部追加系统字段 id / import_batch_id
_DETAIL_HEADERS = list(_LEDGER_HEADERS) + ["ID", "批次ID"]
_DETAIL_KEYS = list(_LEDGER_KEYS) + ["id", "import_batch_id"]

# 金额列（展示时千分位格式化）
_MONEY_KEYS = {"expense_amount", "tax_amount", "book_amount"}
# 只读列（方案 §2.3：id/period/seq/source/import_batch_id 只读；book_amount 自动算只读）
_READONLY_KEYS = {"id", "period", "seq", "source", "import_batch_id", "book_amount"}


def _cell_text(v, is_money: bool) -> str:
    if v is None:
        return ""
    if is_money and isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:,.2f}"
    return str(v)


class ExpenseDetailView(QWidget):
    """账面情况下钻到的「费用台账明细」整页（T3：只读骨架）。"""

    def __init__(self, on_back, parent=None) -> None:
        super().__init__(parent)
        self._on_back = on_back
        self._rows: list[dict] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(scale.px(12))

        # 顶部条：返回 + 标题 + 查看修改记录
        bar = QHBoxLayout()
        bar.setSpacing(scale.px(8))
        self.btn_back = QPushButton("← 返回账面情况")
        self.btn_back.setFixedHeight(scale.px(36))
        self.btn_back.clicked.connect(self._on_back)
        bar.addWidget(self.btn_back)

        self.title = DialogTitleLabel("")
        bar.addWidget(self.title, 1)

        self.btn_log = QPushButton("查看修改记录")
        self.btn_log.setFixedHeight(scale.px(36))
        self.btn_log.setCheckable(True)
        self.btn_log.toggled.connect(self._toggle_log)
        bar.addWidget(self.btn_log)
        lay.addLayout(bar)

        # 明细表（只读）
        self.table = QTableWidget(0, len(_DETAIL_HEADERS))
        self.table.setHorizontalHeaderLabels(_DETAIL_HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)

        # 内嵌审计中心（默认隐藏，点「查看修改记录」显示）；record_id=None = 展示该表全部修改记录
        self._audit = AuditView(self, table_name="expense_ledger", record_id=None, embedded=True)
        self._audit.setVisible(False)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self._audit)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)

        self.hint = CaptionLabel("")
        lay.addWidget(self.hint)

    # ------------------------------------------------------------------ #
    def load(self, s1: str, s2: str, kind: str, months, year) -> None:
        rows = bb.expense_rows_for_cell(str(year), months, s1, s2, kind, get_conn())
        self._rows = rows
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, key in enumerate(_DETAIL_KEYS):
                v = row.get(key)
                item = QTableWidgetItem(_cell_text(v, key in _MONEY_KEYS))
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                if key in _READONLY_KEYS:
                    item.setForeground(style.qcolor("text_mute"))
                self.table.setItem(r, c, item)

        # 标题：按 kind 拼可读标签
        if kind == "grand":
            label = "全部费用"
        elif kind == "uncat":
            label = f"{s1}（未分类）"
        elif kind == "l2":
            label = f"{s1} / {s2}"
        else:  # l1
            label = s1 or "全部费用"
        self.title.setText(f"{year} {label} 明细，共 {len(rows)} 行")
        self.hint.setText(
            "组成「账面情况」对应单元格的全部费用台账行（只读；编辑功能将在后续版本开放）。")

    def _toggle_log(self, on: bool) -> None:
        self._audit.setVisible(on)
        if on:
            self._audit.load()
