"""主窗口：QFluentWidgets FluentWindow（Fluent Design 导航 + 页面栈）"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

from qfluentwidgets import (
    FluentIcon, FluentWindow, InfoBar, InfoBarPosition, NavigationItemPosition,
    SubtitleLabel, BodyLabel,
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


class MainWindow(FluentWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("律所开票收款统计")
        self.resize(1280, 820)
        # Windows 10 无 Mica 材质，自动降级普通背景
        self.setMicaEffectEnabled(False)

        self._build_pages()
        self._build_navigation()

    def _build_pages(self) -> None:
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

    def _build_navigation(self) -> None:
        nav = [
            ("import", self.page_import, FluentIcon.DOWNLOAD, "导入"),
            ("invoice", self.page_invoice, FluentIcon.TILES, "发票收款总表"),
            ("handler_all", self.page_handler_all, FluentIcon.PEOPLE, "经办人收款总表"),
            ("handler_one", self.page_handler_one, FluentIcon.LABEL, "经办人收款表"),
            ("prepayment", self.page_prepayment, FluentIcon.SAVE, "预收款"),
            ("refund", self.page_refund, FluentIcon.CANCEL, "退款"),
            ("manual", self.page_manual, FluentIcon.EDIT, "手动补录"),
            ("staff", self.page_staff, FluentIcon.LIBRARY, "员工管理"),
            ("snapshot", self.page_snapshot, FluentIcon.CAMERA, "快照"),
            ("batch", self.page_batch, FluentIcon.HISTORY, "导入记录"),
        ]
        for key, page, icon, text in nav:
            page.setObjectName(key)
            self.addSubInterface(page, icon, text, NavigationItemPosition.TOP)
        self.navigationInterface.setCurrentItem("import")

    # ---- 对外接口 ----
    def go_to_page(self, key: str) -> None:
        """切换到指定页面并刷新"""
        self.navigationInterface.setCurrentItem(key)
        page = self.navigationInterface.widget(key)
        if page and hasattr(page, "refresh"):
            page.refresh()

    def show_info(self, message: str, success: bool = True) -> None:
        """Fluent 风格通知条"""
        InfoBar.success(message, parent=self, position=InfoBarPosition.TOP_RIGHT, duration=3000) if success \
            else InfoBar.error(message, parent=self, position=InfoBarPosition.TOP_RIGHT, duration=4000)

    def staff_ready(self) -> bool:
        conn = get_conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0] > 0
        finally:
            conn.close()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self.staff_ready():
            InfoBar.warning(
                "首次使用请先在「员工管理」导入职工花名册（模板：职工清单.xlsx），完成初始化后才能导入台账。",
                parent=self, position=InfoBarPosition.TOP, duration=8000,
            )
            self.navigationInterface.setCurrentItem("staff")

    def closeEvent(self, event) -> None:  # noqa: N802
        from app.db import checkpoint
        checkpoint()
        super().closeEvent(event)
