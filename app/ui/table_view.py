"""通用表格视图：Fluent 筛选栏 + 表格 + 导出 Excel"""
from __future__ import annotations

from typing import Callable, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from qfluentwidgets import ComboBox, LineEdit, PrimaryPushButton, PushButton, TableWidget

RED = QColor("#C0392B")    # 红字/负数
GREEN = QColor("#1E8449")  # 已收


class BaseTableView(QWidget):
    """基础表格页：标题 + 筛选栏 + Fluent 表格 + 导出"""

    def __init__(self, title: str, columns: List[str], hint: str = "") -> None:
        super().__init__()
        self.columns = columns
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        from qfluentwidgets import SubtitleLabel, CaptionLabel
        t = SubtitleLabel(title)
        lay.addWidget(t)
        if hint:
            h = CaptionLabel(hint)
            h.setStyleSheet("color:#8A8886;")
            lay.addWidget(h)

        # 筛选栏
        self.filters = QHBoxLayout()
        self.filters.setSpacing(8)
        lay.addLayout(self.filters)
        self._build_filters()

        # Fluent 表格
        self.table = TableWidget(self)
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)

        # 底部
        bottom = QHBoxLayout()
        self.btn_export = PrimaryPushButton("导出 Excel")
        self.btn_export.clicked.connect(self.export_excel)
        self.lbl_summary = CaptionLabel("")
        self.lbl_summary.setStyleSheet("color:#8A8886;")
        bottom.addWidget(self.btn_export)
        bottom.addStretch()
        bottom.addWidget(self.lbl_summary)
        lay.addLayout(bottom)

        self._rows: List[List] = []
        # 构造时主动加载一次数据（子类已重写 load_data）
        self.refresh()

    # ---- 筛选栏（子类覆写）----
    def _build_filters(self) -> None:
        raise NotImplementedError

    # ---- 数据（子类覆写）----
    def load_data(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        self.load_data()
        self._render()

    def showEvent(self, event) -> None:  # noqa: N802
        """导航切换显示时自动刷新（保证数据最新）"""
        super().showEvent(event)
        self.refresh()

    def _render(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            for c, val in enumerate(row):
                item = self._make_item(val, r, c)
                self.table.setItem(r, c, item)
            self.table.setRowHeight(r, 34)
        self.table.setSortingEnabled(True)
        self.lbl_summary.setText(f"共 {len(self._rows)} 行")

    def _make_item(self, val, r: int, c: int):
        from PySide6.QtWidgets import QTableWidgetItem
        item = QTableWidgetItem(self._fmt(val))
        if isinstance(val, (int, float)):
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if val < 0:
                item.setForeground(RED)
            elif val > 0 and c in self._green_cols():
                item.setForeground(GREEN)
        item.setData(Qt.ItemDataRole.UserRole, self._meta.get(r))
        return item

    def _green_cols(self) -> set:
        return set()

    @staticmethod
    def _fmt(v) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:,.2f}"
        return str(v)

    def _meta_at(self, row: int):
        """取某行的元数据（排序后仍正确）"""
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ---- 导出 ----
    def export_excel(self) -> None:
        if not self._rows:
            QMessageBox.information(self, "提示", "当前没有数据可导出")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 Excel", "", "Excel 文件 (*.xlsx)")
        if not path:
            return
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(self.columns)
        for row in self._rows:
            ws.append([None if v is None else v for v in row])
        try:
            wb.save(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出完成", f"已导出 {len(self._rows)} 行到:\n{path}")


def make_filter_widgets(parent: QWidget, filters: QHBoxLayout,
                        on_change: Callable) -> Tuple[ComboBox, ComboBox, QLineEdit, None]:
    """月份 / 来源 / 购方搜索 筛选控件"""
    from qfluentwidgets import CaptionLabel
    filters.addWidget(CaptionLabel("开票月份"))
    month = ComboBox()
    month.addItem("全部月份", userData="")
    for m in _month_options():
        month.addItem(m, userData=m)
    month.currentIndexChanged.connect(on_change)

    filters.addWidget(CaptionLabel("来源"))
    src = ComboBox()
    src.addItem("全部", userData="")
    src.addItem("台账导入", userData="import")
    src.addItem("手动补录", userData="manual")
    src.currentIndexChanged.connect(on_change)

    filters.addWidget(CaptionLabel("购方"))
    buyer = LineEdit()
    buyer.setPlaceholderText("输入购方名称搜索")
    buyer.setFixedWidth(180)
    buyer.textChanged.connect(on_change)

    filters.addWidget(month)
    filters.addWidget(src)
    filters.addWidget(buyer)
    filters.addStretch()
    return month, src, buyer, None


def _month_options() -> List[str]:
    from app.db import get_conn
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT substr(invoice_date,1,7) AS m FROM invoice ORDER BY m"
        ).fetchall()
        return [r["m"] for r in rows]
    finally:
        conn.close()
