"""主窗口：QMainWindow + 自绘 Notion 分组侧栏 + 页面栈（多皮肤框架）

- 侧栏为自绘（非 qfluentwidgets FluentWindow），分组：数据 / 业务 / 结算 / 维护。
- 内容区用 QStackedWidget 承载全部业务视图，逻辑零改动。
- 侧栏底部「皮肤」下拉切换并持久化到 data/prefs.json。
- 保留 go_to_page / show_info / staff_ready / showEvent / closeEvent 等接口，
  以保证 refund_view 等视图里的 window().go_to_page(...) 继续可用。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget, QButtonGroup,
)
from qfluentwidgets import InfoBar, InfoBarPosition

from app.db import get_conn
from app.ui import style
from app.ui.batch_view import BatchView
from app.ui.handler_collect_view import HandlerCollectView
from app.ui.import_view import ImportView
from app.ui.import_verify_view import ImportVerifyView
from app.ui.invoice_collect_view import InvoiceCollectView
from app.ui.invoice_ledger_view import InvoiceLedgerView
from app.ui.invoice_ledger_doc_view import InvoiceLedgerDocView
from app.ui.ledger_view import LedgerView
from app.ui.manual_entry_view import ManualEntryView
from app.ui.prepayment_view import PrepaymentView
from app.ui.refund_view import RefundView
from app.ui.settlement_view import SettlementView
from app.ui.snapshot_view import SnapshotView
from app.ui.staff_view import StaffView
from app.ui.expense_cat_view import ExpenseCatView
from app.ui.data_clear_view import DataClearView
from app.ui.audit_view import AuditView

# 分组导航： (分组标题, [(key, 显示名), ...])
NAV_GROUPS = [
    ("数据", [
        ("import", "导入"),
        ("invoice_ledger", "销项发票"),
        ("ledger_doc", "发票台账"),
        ("ledger", "台账数据"),
        ("batch", "导入记录"),
    ]),
    ("业务", [
        ("invoice", "发票收款情况"),
        ("handler_all", "经办人发票收款情况"),
        ("prepayment", "预收款"),
        ("refund", "退款"),
        ("manual", "发票补录"),
    ]),
    ("结算", [
        ("settlement", "个人结算总表"),
    ]),
    ("维护", [
        ("staff", "员工管理"),
        ("expense_cat", "费用类型维护"),
        ("audit", "修改记录"),
        ("data_clear", "数据清空"),
        ("verify", "导入校验"),
        ("snapshot", "快照"),
    ]),
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("律所开票收款统计")
        self.resize(1280, 820)

        self._build_pages()
        self._build_layout()
        self._apply_current_skin_to_combo()

        # 默认选中导入页
        self.select("import")

    # ------------------------------------------------------------------ #
    # 页面
    # ------------------------------------------------------------------ #
    def _build_pages(self) -> None:
        self.page_import = ImportView()
        self.page_invoice = InvoiceCollectView()
        self.page_handler_all = HandlerCollectView()
        self.page_prepayment = PrepaymentView()
        self.page_refund = RefundView()
        self.page_manual = ManualEntryView()
        self.page_ledger = LedgerView()
        self.page_settlement = SettlementView()
        self.page_staff = StaffView()
        self.page_verify = ImportVerifyView()
        self.page_snapshot = SnapshotView()
        self.page_batch = BatchView()
        self.page_invoice_ledger = InvoiceLedgerView()
        self.page_ledger_doc = InvoiceLedgerDocView()
        self.page_expense_cat = ExpenseCatView()
        self.page_data_clear = DataClearView()
        self.page_audit = AuditView()
        self._pages = {
            "import": self.page_import, "invoice": self.page_invoice,
            "handler_all": self.page_handler_all,
            "prepayment": self.page_prepayment, "refund": self.page_refund,
            "manual": self.page_manual, "ledger": self.page_ledger,
            "settlement": self.page_settlement,
            "staff": self.page_staff,
            "verify": self.page_verify,
            "snapshot": self.page_snapshot, "batch": self.page_batch,
            "invoice_ledger": self.page_invoice_ledger,
            "ledger_doc": self.page_ledger_doc,
            "expense_cat": self.page_expense_cat,
            "data_clear": self.page_data_clear,
            "audit": self.page_audit,
        }
        for key, page in self._pages.items():
            page.setObjectName(key)

    def _build_layout(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.stack = QStackedWidget()
        for page in self._pages.values():
            self.stack.addWidget(page)

        content = QWidget()
        content.setObjectName("pageArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.stack)

        root.addWidget(self._build_sidebar_widget())
        root.addWidget(content, 1)

    # ------------------------------------------------------------------ #
    # 侧栏（自绘）
    # ------------------------------------------------------------------ #
    def _build_sidebar_widget(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(232)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: dict[str, QPushButton] = {}

        for group_title, items in NAV_GROUPS:
            header = QLabel(group_title)
            header.setObjectName("groupHeader")
            layout.addWidget(header)
            for key, label in items:
                btn = QPushButton(label)
                btn.setObjectName("navItem")
                btn.setCheckable(True)
                btn.clicked.connect(lambda _checked=False, k=key: self.select(k))
                self._nav_group.addButton(btn)
                self._nav_buttons[key] = btn
                layout.addWidget(btn)

        layout.addStretch(1)

        # 底部：皮肤切换
        skin_box = QWidget()
        skin_layout = QVBoxLayout(skin_box)
        skin_layout.setContentsMargins(16, 8, 16, 16)
        skin_layout.setSpacing(4)
        skin_label = QLabel("皮肤")
        skin_label.setObjectName("skinLabel")
        self.skin_combo = QComboBox()
        for key, label in style.available_skins():
            self.skin_combo.addItem(label, key)
        self.skin_combo.currentIndexChanged.connect(self._on_skin_changed)
        skin_layout.addWidget(skin_label)
        skin_layout.addWidget(self.skin_combo)
        layout.addWidget(skin_box)

        return sidebar

    def _apply_current_skin_to_combo(self) -> None:
        current = style.load_skin_pref()
        idx = self.skin_combo.findData(current)
        if idx >= 0:
            self.skin_combo.setCurrentIndex(idx)

    def _on_skin_changed(self, index: int) -> None:
        name = self.skin_combo.itemData(index)
        if not name:
            return
        app = QApplication.instance()
        if app is not None:
            style.apply_skin(app, name)
        style.save_skin_pref(name)

    # ------------------------------------------------------------------ #
    # 对外接口（保持与旧 FluentWindow 版兼容）
    # ------------------------------------------------------------------ #
    def select(self, key: str) -> None:
        """切换到指定页面（key 为页面标识）。

        刷新由页面的 showEvent 负责：setCurrentWidget 切换必然触发新页
        showEvent（各视图 showEvent 内已调用 refresh），这里不再显式刷新，
        避免每次点开页面「select + showEvent」双重刷新导致卡顿。
        """
        page = self._pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        btn = self._nav_buttons.get(key)
        if btn is not None:
            btn.setChecked(True)

    def go_to_page(self, key: str) -> None:
        """兼容旧接口：供其他视图通过 window().go_to_page(...) 调用。"""
        self.select(key)

    def show_info(self, message: str, success: bool = True) -> None:
        """Fluent 风格通知条。"""
        if success:
            InfoBar.success(message, parent=self,
                            position=InfoBarPosition.TOP_RIGHT, duration=3000)
        else:
            InfoBar.error(message, parent=self,
                          position=InfoBarPosition.TOP_RIGHT, duration=4000)

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
            self.select("staff")

    def closeEvent(self, event) -> None:  # noqa: N802
        from app.db import checkpoint
        checkpoint()
        super().closeEvent(event)
