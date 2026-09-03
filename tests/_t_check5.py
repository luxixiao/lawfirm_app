"""页头帮助图标集成冒烟（Batch 5）：构造 3 个视图，断言 HelpIcon 已正确挂载。

每个视图独立进程运行，规避 offscreen 下多页共建偶发的 TableColumnLayout 假错
（真实 App 按 _ensure_page 单页懒加载，不受影响）。

运行：
  QT_QPA_PLATFORM=offscreen python tests/_t_check5.py prepayment
  QT_QPA_PLATFORM=offscreen python tests/_t_check5.py import_review
  QT_QPA_PLATFORM=offscreen python tests/_t_check5.py audit
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="helpchk5_"))
import app.ui.style as style  # noqa: E402
style.PREFS_PATH = TMP / "prefs.json"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402
from app.ui.widgets import HelpIcon  # noqa: E402

which = sys.argv[1] if len(sys.argv) > 1 else "prepayment"

app = QApplication.instance() or QApplication([])
style.apply_skin(app, "notion_light")

try:
    if which == "prepayment":
        from app.ui.prepayment_view import PrepaymentView
        v = PrepaymentView()
        v.show()
        app.processEvents()
        # 两个 tab 各一个 ? 图标
        icons = v.findChildren(HelpIcon)
        has_text = [ic for ic in icons if (ic._text or "").strip()]
        # 切换 tab 验证各自激活时可见
        v.setCurrentIndex(0); app.processEvents()
        vis_0 = any(ic.isVisible() for ic in v.findChildren(HelpIcon))
        v.setCurrentIndex(1); app.processEvents()
        vis_1 = any(ic.isVisible() for ic in v.findChildren(HelpIcon))
        ok = len(icons) == 2 and len(has_text) == 2 and vis_0 and vis_1
        print(f"VIEW prepayment: help_icons={len(icons)} with_text={len(has_text)} "
              f"vis_tab0={vis_0} vis_tab1={vis_1} -> {'OK' if ok else 'FAIL'}")
        sys.exit(0 if ok else 1)

    elif which == "import_review":
        from app.ui.import_review_view import ImportReviewView
        v = ImportReviewView()
        v.show()
        app.processEvents()
        icons = v.findChildren(HelpIcon)
        ok = len(icons) == 1 and icons[0].isVisible() and (icons[0]._text or "").strip()
        print(f"VIEW import_review: help_icons={len(icons)} "
              f"visible={icons[0].isVisible() if icons else 'n/a'} -> {'OK' if ok else 'FAIL'}")
        sys.exit(0 if ok else 1)

    elif which == "audit":
        from app.ui.audit_view import AuditView
        v = AuditView()
        v.show()
        app.processEvents()
        icons = v.findChildren(HelpIcon)
        ok = len(icons) == 1 and icons[0].isVisible() and (icons[0]._text or "").strip()
        print(f"VIEW audit: help_icons={len(icons)} "
              f"visible={icons[0].isVisible() if icons else 'n/a'} -> {'OK' if ok else 'FAIL'}")
        sys.exit(0 if ok else 1)

    else:
        print(f"unknown view: {which}")
        sys.exit(2)
except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    print(f"VIEW {which}: CONSTRUCT FAILED -> {e}")
    sys.exit(1)
