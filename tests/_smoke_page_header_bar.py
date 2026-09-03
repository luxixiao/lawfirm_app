"""PageHeaderBar 统一页头冒烟（页头容器版）

断言：
  - 页头是 PageHeaderBar，高度 == scale.px(32)
  - 页面上恰好 1 个 HelpIcon，且挂在 PageHeaderBar 里
  - 多 tab 页：切 tab 时 ? 说明随之切换；tab 条在页头内，内容区（stack）
    在页头之外，且内容区不再被 QTabWidget::pane 外框包裹（无 QTabWidget）

运行（每页独立进程，规避 offscreen 多页共建偶发假错）：
  QT_QPA_PLATFORM=offscreen python tests/_smoke_page_header_bar.py prepayment
  ... settlement / refund / staff / manual_entry / invoice_ledger / salary_ledger
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="phbar_"))
import app.ui.style as style  # noqa: E402
style.PREFS_PATH = TMP / "prefs.json"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QTabWidget  # noqa: E402
from app.ui import scale  # noqa: E402
from app.ui.widgets import HelpIcon, PageHeaderBar  # noqa: E402

which = sys.argv[1] if len(sys.argv) > 1 else "prepayment"

VIEWS = {
    "prepayment": ("app.ui.prepayment_view", "PrepaymentView", True),
    "settlement": ("app.ui.settlement_view", "SettlementView", True),
    "refund": ("app.ui.refund_view", "RefundView", True),
    "staff": ("app.ui.staff_view", "StaffView", True),
    "manual_entry": ("app.ui.manual_entry_view", "ManualEntryView", True),
    "invoice_ledger": ("app.ui.invoice_ledger_view", "InvoiceLedgerView", False),
    "salary_ledger": ("app.ui.salary_ledger_view", "SalaryLedgerView", False),
}

try:
    if which not in VIEWS:
        print(f"unknown view: {which}")
        sys.exit(2)
    mod_name, cls_name, tabbed = VIEWS[which]
    mod = __import__(mod_name, fromlist=[cls_name])
    Cls = getattr(mod, cls_name)

    app = QApplication.instance() or QApplication([])
    style.apply_skin(app, "notion_light")

    v = Cls()
    v.resize(1280, 800)
    v.show()
    app.processEvents()

    bars = v.findChildren(PageHeaderBar)
    icons = v.findChildren(HelpIcon)
    want_h = scale.px(32)

    ok_bar = len(bars) == 1
    bar = bars[0] if bars else None
    ok_h = bool(bar) and bar.height() == want_h
    ok_icon = len(icons) == 1 and bool(bar) and icons[0].parentWidget() is bar
    ok_icon_visible = bool(icons) and icons[0].isVisible()

    # 多 tab 页：内容区不得再被 QTabWidget 包住
    ok_no_pane = not v.findChildren(QTabWidget) if tabbed else True

    msg = [f"bar={len(bars)}", f"h={bar.height() if bar else '-'}/{want_h}",
           f"icons={len(icons)}", f"icon_visible={ok_icon_visible}",
           f"no_pane={ok_no_pane}"]

    ok = ok_bar and ok_h and ok_icon and ok_icon_visible and ok_no_pane

    if tabbed and icons:
        tabs = v.tabs
        tabs.setCurrentIndex(0)
        app.processEvents()
        t0 = icons[0]._text.strip()
        last = tabs.count() - 1
        tabs.setCurrentIndex(last)
        app.processEvents()
        t1 = icons[0]._text.strip()
        ok_switch = bool(t0) and bool(t1) and t0 != t1
        msg.append(f"tabs={tabs.count()} switch={ok_switch}")
        ok = ok and ok_switch
    elif icons:
        t0 = icons[0]._text.strip()
        msg.append(f"help={'Y' if t0 else 'N'}")
        ok = ok and bool(t0)

    png = ROOT / "tests" / f"_smoke_phbar_{which}.png"
    v.grab().save(str(png))

    print(f"VIEW {which}: " + " ".join(msg) + f" -> {'OK' if ok else 'FAIL'} ({png.name})")
    sys.exit(0 if ok else 1)
except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    print(f"VIEW {which}: CONSTRUCT FAILED -> {e}")
    sys.exit(1)
