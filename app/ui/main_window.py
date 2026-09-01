"""主窗口：无边框 FramelessWindow + 自绘 Notion 分组侧栏 + 页面栈（多皮肤框架）

- 窗口框架改为 qframelesswindow.FramelessWindow（无系统标题栏），自建一条 Notion 风
  细标题栏（AppTitleBar，继承库的 TitleBar 以获得拖拽/双击最大化/最小最大关闭按钮），
  保留最大化/最小化/关闭按钮且交互全部由库兜底。
- 侧栏为自绘（非 qfluentwidgets FluentWindow），方案 A 折叠分组：
  单列 6 大类（数据导入 / 台账查看 / 业务数据 / 工资个税 / 各类报表 / 数据维护），
  标题行（图标+组名）点击可独立折叠子项；整体可收起为 60px 图标列。
- 内容区用 QStackedWidget 承载全部业务视图，逻辑零改动。
- 侧栏底部「皮肤」下拉切换并持久化到 data/prefs.json；皮肤切换同步刷新标题栏配色。
- 保留 go_to_page / show_info / staff_ready / showEvent / closeEvent 等接口，
  以保证 refund_view 等视图里的 window().go_to_page(...) 继续可用。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget, QButtonGroup,
)
from qfluentwidgets import InfoBar, InfoBarPosition
from qframelesswindow import FramelessWindow
from qframelesswindow.titlebar import TitleBar

from app.db import get_conn
from app.ui import scale, style
from app.ui.sidebar import SidebarWidget
from app.ui.batch_view import BatchView
from app.ui.calc_sheet_view import CalcSheetView
from app.ui.handler_collect_view import HandlerCollectView
from app.ui.import_view import ImportView
from app.ui.import_verify_view import ImportVerifyView
from app.ui.invoice_collect_view import InvoiceCollectView
from app.ui.invoice_ledger_view import InvoiceLedgerView
from app.ui.invoice_ledger_doc_view import InvoiceLedgerDocView
from app.ui.expense_ledger_view import ExpenseLedgerView
from app.ui.salary_ledger_view import SalaryLedgerView
from app.ui.salary_summary_view import SalarySummaryView
from app.ui.tax_declaration_view import TaxDeclarationView
from app.ui.tax_deduction_view import TaxDeductionView
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
# 分组口径：数据导入 / 台账查看 / 业务数据 / 各类报表 / 数据维护
NAV_GROUPS = [
    ("数据导入", [
        ("import", "导入台账"),
        ("batch", "导入记录"),
        ("verify", "导入校验"),
        ("audit", "修改记录"),
        ("manual", "发票补录"),
    ]),
    ("台账查看", [
        ("invoice_ledger", "销项发票"),
        ("ledger_doc", "发票台账"),
        ("expense_ledger", "费用台账"),
        ("salary_ledger", "工资表"),
    ]),
    ("业务数据", [
        ("invoice", "发票收款情况"),
        ("handler_all", "经办人发票收款情况"),
        ("prepayment", "预收款"),
        ("refund", "退款"),
    ]),
    ("工资个税", [
        ("salary_summary", "工资累计"),
        ("tax_declaration", "1-11月个税申报"),
        ("tax_deduction", "费用扣除"),
    ]),
    ("分成计算", [
        ("calc", "计算表"),
    ]),
    ("各类报表", [
        ("settlement", "各类报表"),
    ]),
    ("数据维护", [
        ("staff", "员工管理"),
        ("expense_cat", "费用类型"),
        ("data_clear", "数据情况"),
        ("snapshot", "快照"),
    ]),
]


def _titlebar_btn_hover_bg(p: dict) -> QColor:
    """窗口按钮 hover 背景：浅色用淡黑、深色用淡白（库默认 hover 黑在深色下几乎不可见）。"""
    return QColor(0, 0, 0, 26) if p.get("bg", "#fff").lower() != "#1f1f1e" else QColor(255, 255, 255, 26)


def _titlebar_btn_press_bg(p: dict) -> QColor:
    return QColor(0, 0, 0, 51) if p.get("bg", "#fff").lower() != "#1f1f1e" else QColor(255, 255, 255, 51)


class AppTitleBar(TitleBar):
    """无边框窗口的自定义标题栏：继承库 TitleBar（自带最小/最大/关闭按钮 + 拖拽 + 双击最大化），
    左侧动态显示当前页面名（无品牌文字、无图标），整体 Notion 风，配色跟随皮肤。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(scale.px(36))
        self.titleLabel = QLabel("")
        self.titleLabel.setObjectName("titleBarTitle")
        self.titleLabel.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.hBoxLayout.insertWidget(0, self.titleLabel, 0, Qt.AlignLeft)
        self._full_title = ""
        self.apply_skin()

    def set_page_title(self, name: str) -> None:
        """设置当前页面名（来自 NAV_GROUPS 显示名）。"""
        self._full_title = name or ""
        self._elide()

    def _elide(self) -> None:
        """按可用宽度对长标题做末尾省略截断，避免压到右侧按钮。"""
        if not self._full_title:
            self.titleLabel.setText("")
            return
        fm = self.titleLabel.fontMetrics()
        # 右侧三按钮各 46px + 间距 + 左 padding(12) + 余量（随字号缩放）
        max_w = max(scale.px(60), self.width() - scale.px(150))
        self.titleLabel.setText(fm.elidedText(self._full_title, Qt.ElideRight, max_w))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def apply_skin(self) -> None:
        p = style.palette()
        # 标题栏底色取侧栏色：它在侧栏正上方，用 bg_side 与左侧侧栏无缝衔接；
        # 右侧落在内容区(bg)之上，有 1px 分隔线过渡，比纯白 bg 更协调。
        bar_bg = p.get("bg_side", "#F7F7F5")
        text = p.get("text", "#1f1f1f")
        border = p.get("border", "#E9E9E7")
        # border-bottom 与内容区分层；分隔线色取中性描边
        self.setStyleSheet(
            f"background:{bar_bg}; border:none; border-bottom:1px solid {border};"
        )
        self.titleLabel.setStyleSheet(
            f"color:{text}; font:{scale.sp(13)}px 'Microsoft YaHei'; "
            f"padding-left:{scale.px(12)}px;"
        )
        # 三按钮为自定义绘制（paintEvent），用属性设色而非 QSS。
        # 图标色用文字色：修复深色皮肤下默认纯黑图标不可见的问题；hover/按下态按皮肤给可见底色。
        hover = _titlebar_btn_hover_bg(p)
        press = _titlebar_btn_press_bg(p)
        for btn in (self.minBtn, self.maxBtn):
            btn.setNormalColor(QColor(text))
            btn.setHoverColor(QColor(text))
            btn.setPressedColor(QColor(text))
            btn.setNormalBackgroundColor(QColor(0, 0, 0, 0))
            btn.setHoverBackgroundColor(hover)
            btn.setPressedBackgroundColor(press)
        # 关闭按钮保留 Windows 风红底 + 白图标
        self.closeBtn.setNormalColor(QColor(text))
        self.closeBtn.setHoverColor(Qt.white)
        self.closeBtn.setPressedColor(Qt.white)
        self.closeBtn.setNormalBackgroundColor(QColor(0, 0, 0, 0))
        self.closeBtn.setHoverBackgroundColor(QColor(232, 17, 35))
        self.closeBtn.setPressedBackgroundColor(QColor(241, 112, 122))


class MainWindow(FramelessWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("律所开票收款统计")
        self.setTitleBar(AppTitleBar(self))
        self.resize(1280, 820)
        # 不给主窗口设大下限：某些页面（宽表格）的 minimumSizeHint 可达 1700+，
        # 会超过 1600 宽的屏幕，Windows 拒绝设置几何并反复重试，刷 setGeometry 告警。
        self.setMinimumSize(1100, 650)

        self._build_pages()
        self._build_layout()
        self._apply_current_skin_to_combo()
        self._install_font_shortcuts()

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
        self.page_expense_ledger = ExpenseLedgerView()
        self.page_salary_ledger = SalaryLedgerView()
        self.page_salary_summary = SalarySummaryView()
        self.page_tax_declaration = TaxDeclarationView()
        self.page_tax_deduction = TaxDeductionView()
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
        self.page_calc = CalcSheetView()
        # 注：原「工资个税」占位的 payroll 页已由真正的 SalarySummaryView 取代，
        # _PlaceholderPage 类保留备用（后续若新增未实现模块可直接复用）。
        self._pages = {
            "import": self.page_import, "invoice": self.page_invoice,
            "handler_all": self.page_handler_all,
            "prepayment": self.page_prepayment, "refund": self.page_refund,
            "manual": self.page_manual, "expense_ledger": self.page_expense_ledger,
            "salary_ledger": self.page_salary_ledger,
            "settlement": self.page_settlement,
            "staff": self.page_staff,
            "verify": self.page_verify,
            "snapshot": self.page_snapshot, "batch": self.page_batch,
            "invoice_ledger": self.page_invoice_ledger,
            "ledger_doc": self.page_ledger_doc,
            "expense_cat": self.page_expense_cat,
            "data_clear": self.page_data_clear,
            "audit": self.page_audit,
            "salary_summary": self.page_salary_summary,
            "tax_declaration": self.page_tax_declaration,
            "tax_deduction": self.page_tax_deduction,
            "calc": self.page_calc,
        }
        for key, page in self._pages.items():
            page.setObjectName(key)

    def _build_layout(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.stack = QStackedWidget()
        # QStackedWidget 的 minimumSizeHint = 所有页面里最大的那个，宽表格页面会把它顶到
        # 1700+，进而把主窗口最小宽度撑得比屏幕还宽。显式归零，让页面自己出滚动条。
        self.stack.setMinimumSize(0, 0)
        for page in self._pages.values():
            self.stack.addWidget(page)

        content = QWidget()
        content.setObjectName("pageArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.stack)

        self._key_to_group = {k: t for t, items in NAV_GROUPS for k, _ in items}
        self._key_to_title = {k: name for _, items in NAV_GROUPS for k, name in items}
        self.skin_box = self._build_skin_box()
        self.sidebar = SidebarWidget(NAV_GROUPS, bottom_widget=self.skin_box)
        self.sidebar.itemSelected.connect(self.select)

        root.addWidget(self.sidebar)
        root.addWidget(content, 1)

        # FramelessWindow 是 QWidget：内容放进自身布局，顶部为标题栏留白
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        self.root_layout.setSpacing(0)
        self.root_layout.addWidget(central, 1)

    # ------------------------------------------------------------------ #
    # 侧栏（方案 C 双栏，见 app/ui/sidebar.py）
    # ------------------------------------------------------------------ #
    def _build_skin_box(self) -> QWidget:
        """原侧栏底部的皮肤切换挂件，现挂到双栏侧栏右栏底部。"""
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

        self.motion_box = QCheckBox("动效")
        self.motion_box.setChecked(style.motion_enabled())
        self.motion_box.setToolTip("关闭可减少画面移动，适合前庭敏感用户")
        self.motion_box.toggled.connect(self._on_motion_toggled)
        skin_layout.addWidget(self.motion_box)

        font_label = QLabel("字号")
        font_label.setObjectName("skinLabel")
        self.font_combo = QComboBox()
        for i, size, label in scale.steps():
            self.font_combo.addItem(f"{label}（{size}px）", i)
        self.font_combo.setCurrentIndex(scale.step())
        self.font_combo.setToolTip(
            "全局字号 5 档，即时生效；快捷键 Ctrl+= 放大 / Ctrl+- 缩小 / Ctrl+0 复位")
        self.font_combo.currentIndexChanged.connect(self._on_font_changed)
        skin_layout.addWidget(font_label)
        skin_layout.addWidget(self.font_combo)
        return skin_box

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
        if getattr(self, "titleBar", None) is not None:
            self.titleBar.apply_skin()

    def _on_motion_toggled(self, enabled: bool) -> None:
        style.set_motion_enabled(enabled)
        self.sidebar.apply_motion_pref()
        if enabled:
            self.show_info("动效已开启", success=True)
        else:
            self.show_info("动效已关闭：界面切换将瞬时到位", success=True)

    def _on_font_changed(self, index: int) -> None:
        if index < 0:
            return
        self.apply_font_step(index)

    def _install_font_shortcuts(self) -> None:
        """Ctrl+= 放大一档 / Ctrl+- 缩小一档 / Ctrl+0 复位标准档。"""
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Ctrl+="), self,
                  activated=lambda: self.apply_font_step(scale.step() + 1))
        QShortcut(QKeySequence("Ctrl+-"), self,
                  activated=lambda: self.apply_font_step(scale.step() - 1))
        QShortcut(QKeySequence("Ctrl+0"), self,
                  activated=lambda: self.apply_font_step(scale.DEFAULT_STEP))

    def apply_font_step(self, step: int) -> None:
        """切换全局字号档位并即时生效。

        顺序：scale 真源 → QSS 按新倍率重建 → 标题栏/侧栏重算几何 →
        当前页立即 refresh（行高/列宽按新档位重建）；其余页在下次
        showEvent 时由既有刷新机制自动按新档位重建。
        """
        step = max(0, min(len(scale.STEPS) - 1, int(step)))
        if step == scale.step():
            return
        scale.set_step(step)
        scale.save_step(step)
        app = QApplication.instance()
        if app is not None:
            style.refresh_qss(app)
        if getattr(self, "titleBar", None) is not None:
            self.titleBar.apply_skin()
        self.sidebar.reapply_metrics()
        page = self.stack.currentWidget() if getattr(self, "stack", None) else None
        if page is not None and hasattr(page, "refresh"):
            page.refresh()
        self.show_info(f"字号已切换为「{scale.LABELS[step]}」", success=True)

    # ------------------------------------------------------------------ #
    # 对外接口（保持与旧 FluentWindow 版兼容）
    # ------------------------------------------------------------------ #
    def select(self, key: str) -> None:
        """切换到指定页面（key 为页面标识）。

        刷新由页面的 showEvent 负责：setCurrentWidget 切换必然触发新页
        showEvent（各视图 showEvent 内已调用 refresh），这里不再显式刷新，
        避免每次点开页面「select + showEvent」双重刷新导致卡顿。
        同时联动双栏侧栏：确保右侧显示对应大类并高亮子项。
        """
        page = self._pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        grp = self._key_to_group.get(key)
        if grp is not None:
            self.sidebar.activate(key, grp)
        # 同步标题栏：显示当前页面名（B 方案）
        if getattr(self, "titleBar", None) is not None:
            self.titleBar.set_page_title(self._key_to_title.get(key, ""))

    def go_to_page(self, key: str) -> None:
        """兼容旧接口：供其他视图通过 window().go_to_page(...) 调用。"""
        self.select(key)

    def show_info(self, message: str, success: bool = True) -> None:
        """Fluent 风格通知条。

        注意 qfluentwidgets 签名：InfoBar.success(title, content, ...) 前两个
        位置参数是标题+内容，标题传空串只显示正文。
        """
        if success:
            InfoBar.success("", message, parent=self,
                            position=InfoBarPosition.TOP_RIGHT, duration=3000)
        else:
            InfoBar.error("", message, parent=self,
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
                "", "首次使用请先在「员工管理」导入职工花名册（模板：职工清单.xlsx），完成初始化后才能导入台账。",
                parent=self, position=InfoBarPosition.TOP, duration=8000,
            )
            self.select("staff")

    def closeEvent(self, event) -> None:  # noqa: N802
        from app.db import checkpoint
        checkpoint()
        super().closeEvent(event)


class _PlaceholderPage(QWidget):
    """暂未实现模块的占位页（如工资个税）。"""

    def __init__(self, text: str) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        lab = QLabel(text)
        lab.setObjectName("placeholder")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(lab)
        lay.addStretch(1)
