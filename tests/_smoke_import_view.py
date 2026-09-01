"""offscreen 冒烟：ImportView 改为 QTabWidget（导入确认嵌入 tab）。

覆盖：
- ImportView 是 QTabWidget，含「导入」「导入确认」两个 tab
- 发票台账导入时不再弹模态框，而是把数据载入嵌入面板并切到「导入确认」tab
- 面板 confirmed → _on_confirmed 写库（commit_ledger_import）并切回「导入」tab
- 面板 cancelled → _on_cancelled 直接切回「导入」tab，不写库

为不碰真实文件/数据库，parse_ledger_file 与 commit_ledger_import 均被打桩。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QTabWidget

app = QApplication.instance() or QApplication(sys.argv)

import app.ui.import_view as IV  # noqa: E402
from app.ui.import_view import ImportView  # noqa: E402
from app.importer import importer as _imp  # noqa: E402


# ---- 打桩：避免碰真实文件 / 数据库（Seafile 同步库在沙箱会被锁）----
class _FakeCursor:
    """模拟 sqlite 游标：可迭代（返回空行）+ fetchall + 链式 execute。"""
    def __iter__(self):
        return iter([])

    def fetchall(self):
        return []


class _FakeConn:
    def execute(self, *a, **k):
        return _FakeCursor()

    def fetchall(self):
        return []

    def commit(self):
        pass

    def close(self):
        pass


class _MB:
    """offscreen 下 QMessageBox 会阻塞，用空桩替代。"""
    @staticmethod
    def warning(*a, **k):
        return None

    @staticmethod
    def question(*a, **k):
        return 1  # Yes

    @staticmethod
    def information(*a, **k):
        return None


_fake_data = {
    "invoices": [], "prepayments": [], "problems": [],
    "sheet_totals": {}, "sheet12_total": 0.0, "period": "2025-01",
}
_commit_calls = []


def _fake_parse(path, period):
    return dict(_fake_data, period=period)


def _fake_commit(data, period, path):
    _commit_calls.append((data, period, path))
    return {"type": "ledger", "period": period, "invoice_count": 0,
            "prepayment_count": 0, "sheet12_total": 0.0}


def _fake_validate(data, period):
    return None


import PySide6.QtWidgets as _qt  # noqa: E402
from app.ui import unified_import_dialog as _uid  # noqa: E402
_qt.QMessageBox = _MB
IV.QMessageBox = _MB
_uid.QMessageBox = _MB


_imp.parse_ledger_file = _fake_parse
_imp.commit_ledger_import = _fake_commit
_imp.validate_ledger_before_write = _fake_validate
IV.parse_ledger_file = _fake_parse
IV.commit_ledger_import = _fake_commit
IV.validate_ledger_before_write = _fake_validate
IV.get_conn = lambda: _FakeConn()


results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}", flush=True)


# ---------------------------------------------------------------- 1) 结构
view = ImportView()
check("ImportView 是 QTabWidget", isinstance(view, QTabWidget))
check("共 2 个 tab", view.count() == 2, f"got={view.count()}")
check("tab0=导入", view.tabText(0) == "导入", view.tabText(0))
check("tab1=导入确认", view.tabText(1) == "导入确认", view.tabText(1))
check("嵌入面板为 UnifiedImportDialog",
      view._confirm_panel.__class__.__name__ == "UnifiedImportDialog")
check("初始停留在「导入」tab",
      view.currentWidget() is view._tab_import)

# ---------------------------------------------------------------- 2) 导入台账 → 切到确认 tab
_commit_calls.clear()
view._do_import("2025.1台账.xlsx")
check("导入台账后切到「导入确认」tab",
      view.currentWidget() is view._tab_confirm)
check("面板已载入数据（load_data 生效）",
      view._confirm_panel._data.get("period") == "2025-01",
      str(view._confirm_panel._data.get("period")))
check("pending 已记录", view._pending == ("2025-01", "2025.1台账.xlsx"),
      str(view._pending))
check("尚未写库（等确认）", len(_commit_calls) == 0)

# ---------------------------------------------------------------- 3) 确认 → 写库 + 切回
view._confirm_panel.accept()
check("确认后写库一次", len(_commit_calls) == 1, f"got={len(_commit_calls)}")
check("切回「导入」tab", view.currentWidget() is view._tab_import)
if _commit_calls:
    _, p, path = _commit_calls[0]
    check("写库参数（账期/路径）正确", (p, path) == ("2025-01", "2025.1台账.xlsx"),
          f"{(p, path)}")
check("pending 已清空", view._pending is None)

# ---------------------------------------------------------------- 4) 取消 → 不写库 + 切回
view._do_import("2025.2台账.xlsx")
check("第二次导入切到确认 tab", view.currentWidget() is view._tab_confirm)
before = len(_commit_calls)
view._confirm_panel.reject()
check("取消后不写库", len(_commit_calls) == before, f"got={len(_commit_calls)}")
check("取消后切回「导入」tab", view.currentWidget() is view._tab_import)
check("pending 已清空", view._pending is None)

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
