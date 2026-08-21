"""通用表格视图：筛选栏 + 表格 + 导出 Excel"""
from __future__ import annotations

from typing import Callable, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

RED = QColor("#C0392B")   # 红字/退款/负数
GREEN = QColor("#1E8449")  # 已收


class BaseTableView(QWidget):
    """基础表格页：标题 + 筛选栏 + 数据表 + 导出"""

    def __init__(self, title: str, columns: List[str], hint: str = "") -> None:
        super().__init__()
        self.columns = columns
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        t = QLabel(title)
        t.setObjectName("pageTitle")
        lay.addWidget(t)
        if hint:
            h = QLabel(hint)
            h.setObjectName("pageHint")
            lay.addWidget(h)

        # 筛选栏
        self.filters = QHBoxLayout()
        self.filters.setSpacing(8)
        lay.addLayout(self.filters)
        self._build_filters()

        # 表格
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        head.setStretchLastSection(True)
        lay.addWidget(self.table)

        # 底部操作
        bottom = QHBoxLayout()
        self.btn_export = QPushButton("导出 Excel")
        self.btn_export.clicked.connect(self.export_excel)
        self.lbl_summary = QLabel("")
        self.lbl_summary.setStyleSheet("color:#787774;")
        bottom.addWidget(self.btn_export)
        bottom.addStretch()
        bottom.addWidget(self.lbl_summary)
        lay.addLayout(bottom)

        self._rows: List[List] = []

    # ---- 筛选栏（子类覆写）----
    def _build_filters(self) -> None:
        raise NotImplementedError

    # ---- 数据（子类覆写）----
    def load_data(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        self.load_data()
        self._render()

    def _render(self) -> None:
        self.table.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            for c, val in enumerate(row):
                item = QTableWidgetItem(self._fmt(val))
                if isinstance(val, (int, float)) and val < 0:
                    item.setForeground(RED)
                elif isinstance(val, float) and val > 0 and c in self._green_cols():
                    item.setForeground(GREEN)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, r)
                self.table.setItem(r, c, item)
        self.lbl_summary.setText(f"共 {len(self._rows)} 行")

    @staticmethod
    def _fmt(v) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:,.2f}"
        return str(v)

    def _green_cols(self) -> set:
        return set()

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
                        on_change: Callable) -> Tuple[QComboBox, QComboBox, QLineEdit, QComboBox]:
    """月份 / 来源 / 购方搜索 / 经办人 筛选控件（经办人可 None）"""
    lbl_m = QLabel("开票月份")
    month = QComboBox()
    month.addItem("全部月份", "")
    for m in _month_options():
        month.addItem(m, m)
    month.currentIndexChanged.connect(on_change)

    lbl_s = QLabel("来源")
    src = QComboBox()
    src.addItem("全部", "")
    src.addItem("台账导入", "import")
    src.addItem("手动补录", "manual")
    src.currentIndexChanged.connect(on_change)

    lbl_b = QLabel("购方")
    buyer = QLineEdit()
    buyer.setPlaceholderText("输入购方名称搜索")
    buyer.setFixedWidth(180)
    buyer.textChanged.connect(on_change)

    filters.addWidget(lbl_m)
    filters.addWidget(month)
    filters.addWidget(lbl_s)
    filters.addWidget(src)
    filters.addWidget(lbl_b)
    filters.addWidget(buyer)
    filters.addStretch()
    return month, src, buyer, None


def _month_options() -> List[str]:
    """从数据库取已导入的月份（开票日期去重）"""
    from app.db import get_conn
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT substr(invoice_date,1,7) AS m FROM invoice ORDER BY m"
        ).fetchall()
        return [r["m"] for r in rows]
    finally:
        conn.close()
