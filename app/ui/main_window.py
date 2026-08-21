"""主窗口：Notion-like 布局 + 左侧导航 + 页面栈"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QStackedWidget, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.ui.batch_view import BatchView
from app.ui.handler_collect_view import HandlerCollectView
from app.ui.handler_filter_view import HandlerFilterView
from app.ui.import_view import ImportView
from app.ui.invoice_collect_view import InvoiceCollectView
from app.ui.manual_entry_view import ManualEntryView
from app.ui.prepayment_view import PrepaymentView
from app.ui.refund_view import RefundView
from app.ui.snapshot_view import SnapshotView
from app.ui.staff_view import StaffView

QSS = """
QMainWindow, QWidget { background: #FFFFFF; color: #37352F; font-size: 13px; }
#sidebar { background: #F7F7F5; border-right: 1px solid #EBEBE8; }
#sidebar QLabel#app_title { color: #37352F; font-size: 15px; font-weight: 600; padding: 16px 16px 8px 16px; }
#sidebar QListWidget { background: transparent; border: none; outline: none; padding: 4px 8px; }
#sidebar QListWidget::item { padding: 8px 12px; border-radius: 6px; color: #37352F; margin: 1px 0; }
#sidebar QListWidget::item:hover { background: #EFEFEC; }
#sidebar QListWidget::item:selected { background: #E9E9E7; color: #37352F; font-weight: 500; }
#pageArea { background: #FFFFFF; }
#pageTitle { font-size: 18px; font-weight: 600; padding: 20px 24px 4px 24px; }
#pageHint { color: #787774; padding: 0 24px; }
#placeholder { color: #B3B1AD; font-size: 14px; padding: 40px; }
QPushButton { background: #F1F1EF; border: 1px solid #DADAD7; border-radius: 6px; padding: 6px 14px; color: #37352F; }
QPushButton:hover { background: #E9E9E7; }
QPushButton#primary { background: #37352F; color: #FFFFFF; border: none; }
QPushButton#primary:hover { background: #4F4D49; }
QTableWidget { gridline-color: #EEEEEC; border: 1px solid #EBEBE8; border-radius: 6px; }
QHeaderView::section { background: #F7F7F5; color: #787774; border: none; border-bottom: 1px solid #EBEBE8; padding: 6px 8px; font-weight: 500; }
QTableWidget::item { padding: 4px 8px; }
"""


def _placeholder(title: str) -> QWidget:
    """占位页面"""
    w = QWidget()
    lay = QVBoxLayout(w)
    lbl = QLabel(title)
    lbl.setObjectName("pageTitle")
    hint = QLabel("该模块开发中，敬请期待。")
    hint.setObjectName("pageHint")
    lay.addWidget(lbl)
    lay.addWidget(hint)
    lay.addStretch()
    return w


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("律所开票收款统计")
        self.resize(1280, 800)
        self.setStyleSheet(QSS)
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 左侧导航 ----
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        s_lay = QVBoxLayout(sidebar)
        s_lay.setContentsMargins(0, 0, 0, 0)
        s_lay.setSpacing(0)

        title = QLabel("📊 律所开票收款")
        title.setObjectName("app_title")
        s_lay.addWidget(title)

        self.nav = QListWidget()
        self._nav_items = [
            ("导入", "import"),
            ("发票收款总表", "invoice"),
            ("经办人发票收款总表", "handler_all"),
            ("经办人发票收款表", "handler_one"),
            ("预收款", "prepayment"),
            ("退款", "refund"),
            ("手动补录", "manual"),
            ("员工管理", "staff"),
            ("快照", "snapshot"),
            ("导入记录", "batch"),
        ]
        for label, key in self._nav_items:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._on_nav)
        s_lay.addWidget(self.nav)
        s_lay.addStretch()

        # ---- 右侧页面栈 ----
        self.pages = QStackedWidget()
        self.pages.setObjectName("pageArea")

        self.page_import = ImportView()
        self.page_invoice = InvoiceCollectView()
        self.page_handler_all = HandlerCollectView()
        self.page_handler_one = HandlerFilterView()
        self.page_prepayment = PrepaymentView()
        self.page_refund = RefundView()
        self.page_manual = ManualEntryView()
        self.page_staff = StaffView()
        self.page_snapshot = SnapshotView()
        self.page_batch = BatchView()

        for p in (self.page_import, self.page_invoice, self.page_handler_all,
                  self.page_handler_one, self.page_prepayment, self.page_refund,
                  self.page_manual, self.page_staff, self.page_snapshot, self.page_batch):
            self.pages.addWidget(p)

        root.addWidget(sidebar)
        root.addWidget(self.pages, 1)
        self.setCentralWidget(central)

        self.nav.setCurrentRow(0)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 初始化引导：花名册为空时提示
        if not self.staff_ready():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "初始化",
                "首次使用请先在「员工管理」导入职工花名册（模板：职工清单.xlsx），\n"
                "完成初始化后才能导入台账。",
            )
            self.nav.setCurrentRow(7)

    def _on_nav(self, row: int) -> None:
        self.pages.setCurrentIndex(max(row, 0))
        page = self.pages.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()

    # ---- 供其他模块调用 ----
    def staff_ready(self) -> bool:
        """花名册是否已初始化（staff 表非空）"""
        conn = get_conn()
        try:
            n = conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0]
            return n > 0
        finally:
            conn.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        """退出时 checkpoint，保证 WAL 合并回主库（同步软件前）"""
        from app.db import checkpoint
        checkpoint()
        super().closeEvent(event)
