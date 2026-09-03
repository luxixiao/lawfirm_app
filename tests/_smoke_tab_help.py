"""多 tab 页 corner ? 冒烟（标题统一精简版）：逐页构造，断言 tab 条右上角 HelpIcon
存在/可见，且切 tab 后说明文字随之切换。

每个视图独立进程运行，规避 offscreen 下多页共建偶发的 TableColumnLayout 假错。

运行：
  QT_QPA_PLATFORM=offscreen python tests/_smoke_tab_help.py prepayment
  QT_QPA_PLATFORM=offscreen python tests/_smoke_tab_help.py settlement
  QT_QPA_PLATFORM=offscreen python tests/_smoke_tab_help.py refund
  QT_QPA_PLATFORM=offscreen python tests/_smoke_tab_help.py staff
  QT_QPA_PLATFORM=offscreen python tests/_smoke_tab_help.py manual_entry
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="tabhelp_"))
import app.ui.style as style  # noqa: E402
style.PREFS_PATH = TMP / "prefs.json"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402
from app.ui.widgets import HelpIcon  # noqa: E402

which = sys.argv[1] if len(sys.argv) > 1 else "prepayment"

VIEWS = {
    "prepayment": ("app.ui.prepayment_view", "PrepaymentView"),
    "settlement": ("app.ui.settlement_view", "SettlementView"),
    "refund": ("app.ui.refund_view", "RefundView"),
    "staff": ("app.ui.staff_view", "StaffView"),
    "manual_entry": ("app.ui.manual_entry_view", "ManualEntryView"),
}

try:
    if which not in VIEWS:
        print(f"unknown view: {which}")
        sys.exit(2)
    mod_name, cls_name = VIEWS[which]
    mod = __import__(mod_name, fromlist=[cls_name])
    Cls = getattr(mod, cls_name)

    app = QApplication.instance() or QApplication([])
    style.apply_skin(app, "notion_light")

    v = Cls()
    v.show()
    app.processEvents()

    # 多 tab 页：视图本身即 QTabWidget（prepayment）或含 .tabs（其余）
    tabs = v if hasattr(v, "count") else v.tabs
    corner = tabs.cornerWidget()
    ok_corner = isinstance(corner, HelpIcon)
    icons = v.findChildren(HelpIcon)

    tabs.setCurrentIndex(0)
    app.processEvents()
    t0 = (corner._text if corner else "").strip()

    last = tabs.count() - 1
    tabs.setCurrentIndex(last)
    app.processEvents()
    t1 = (corner._text if corner else "").strip()

    ok = (len(icons) == 1 and ok_corner and icons[0].isVisible()
          and bool(t0) and bool(t1) and t0 != t1)
    print(f"VIEW {which}: help_icons={len(icons)} corner_is_helpicon={ok_corner} "
          f"visible={icons[0].isVisible() if icons else 'n/a'} "
          f"tab0_help={'Y' if t0 else 'N'} tab{last}_help={'Y' if t1 else 'N'} "
          f"switched={t0 != t1} -> {'OK' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    print(f"VIEW {which}: CONSTRUCT FAILED -> {e}")
    sys.exit(1)
