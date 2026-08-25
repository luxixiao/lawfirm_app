"""费用类型维护页（原「台账数据→费用归类」Tab 迁移至维护组，需求 2.1）

- 展示费用类型全集（来自 expense_ledger 同步），可调整归类、调整顺序、新增类型。
- 与结算/年度聘用结算表取数逻辑一致（按 expense_cat.sort_order / category）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.ui.widgets import (CaptionLabel, PushButton, SubtitleLabel)
from app.ui.column_state import attach_persistence, restore_col_widths
from app.db import get_conn
from app.engine.change_log import log_change
from app.engine.expense_cat import (CATEGORIES, add_type, ensure_types, get_by_category,
                                    move_type, set_category, sync_from_ledger)


class ExpenseCatView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        t = SubtitleLabel("费用类型维护")
        lay.addWidget(t)
        h = CaptionLabel("费用类型全集与归类（报酬发放/住房公积金/保险费/汽油费/其他）。"
                          "顺序与归类会影响结算表分组。修改仅记录类型配置，不影响发票台账修改记录。")
        lay.addWidget(h)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["顺序", "费用类型", "归类", "说明"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(2, 130)
        attach_persistence(self.table, "expense_cat", "main")
        lay.addWidget(self.table, 1)

        bar = QHBoxLayout()
        self.btn_up = PushButton("上移")
        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_down = PushButton("下移")
        self.btn_down.clicked.connect(lambda: self._move(1))
        bar.addWidget(self.btn_up)
        bar.addWidget(self.btn_down)
        bar.addSpacing(12)
        bar.addWidget(CaptionLabel("新增类型"))
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("费用类型名称")
        self.new_name.setMaximumWidth(200)
        bar.addWidget(self.new_name)
        self.new_cat = QComboBox()
        for c in CATEGORIES:
            self.new_cat.addItem(c, userData=c)
        bar.addWidget(self.new_cat)
        btn_add = PushButton("添加")
        btn_add.clicked.connect(self._add)
        bar.addWidget(btn_add)
        bar.addStretch()
        lay.addLayout(bar)

        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        sync_from_ledger()
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT expense_type, category FROM expense_cat ORDER BY sort_order, expense_type").fetchall()
        finally:
            conn.close()
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            seq_item = QTableWidgetItem(str(r + 1))
            seq_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(r, 0, seq_item)
            t_item = QTableWidgetItem(row["expense_type"])
            t_item.setData(Qt.ItemDataRole.UserRole, row["expense_type"])
            self.table.setItem(r, 1, t_item)
            combo = QComboBox()
            for c in CATEGORIES:
                combo.addItem(c, userData=c)
            combo.setCurrentText(row["category"] or "其他")
            combo.currentIndexChanged.connect(
                lambda *_, row=r, etype=row["expense_type"]: self._change(row, etype))
            self.table.setCellWidget(r, 2, combo)
            types = get_by_category(row["category"])
            self.table.setItem(r, 3, QTableWidgetItem("、".join(types)))
            self.table.setRowHeight(r, 34)
        if restore_col_widths(self.table, "expense_cat", "main"):
            self.table.horizontalHeader().setStretchLastSection(False)

    def _move(self, direction: int) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "提示", "请先选中要移动的费用类型")
            return
        r = rows[0].row()
        item = self.table.item(r, 1)
        if item is None:
            return
        etype = item.data(Qt.ItemDataRole.UserRole)
        if not etype:
            return
        move_type(etype, direction)
        self.refresh()
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 1)
            if it and it.text() == etype:
                self.table.selectRow(i)
                break

    def _change(self, row: int, etype: str) -> None:
        combo = self.table.cellWidget(row, 2)
        if combo is None:
            return
        new_cat = combo.currentData()
        conn = get_conn()
        try:
            old_cat = conn.execute("SELECT category FROM expense_cat WHERE expense_type=?", (etype,)).fetchone()
            old = old_cat["category"] if old_cat else "其他"
            if old == new_cat:
                return
            set_category(etype, new_cat)
            log_change(conn, "expense_cat", etype, "category", old, new_cat, "费用类型维护")
            conn.commit()
        finally:
            conn.close()
        for i in range(self.table.rowCount()):
            combo_i = self.table.cellWidget(i, 2)
            if combo_i and combo_i.currentData() == new_cat:
                self.table.setItem(i, 3, QTableWidgetItem("、".join(get_by_category(new_cat))))

    def _add(self) -> None:
        name = self.new_name.text().strip()
        if not name:
            QMessageBox.information(self, "提示", "请输入费用类型名称")
            return
        cat = self.new_cat.currentData()
        add_type(name, cat)
        self.new_name.clear()
        self.refresh()
