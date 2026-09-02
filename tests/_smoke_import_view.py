"""offscreen 冒烟：ImportView（导入复核页改造 阶段 1 后）。

覆盖：
- ImportView 退回 QWidget（原 QTabWidget 的「导入确认」tab 已移除）
- 发票台账导入解析后通过 ledger_pending 信号交给「导入复核」页，本页不写库
- log_result 桥接导入复核页回传的结果（写导入日志）

为不碰真实文件/数据库，parse_ledger_file 打桩、get_conn 用假连接。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

app = QApplication.instance() or QApplication(sys.argv)

import app.ui.import_view as IV  # noqa: E402
from app.ui.import_view import ImportView  # noqa: E402
from app.importer import importer as _imp  # noqa: E402


# ---- 打桩：避免碰真实文件 / 数据库（Seafile 同步库在沙箱会被锁）----
class _FakeCursor:
    def __iter__(self):
        return iter([])

    def fetchall(self):
        return []


class _FakeConn:
    def execute(self, *a, **k):
        return _FakeCursor()

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
        return 1

    @staticmethod
    def information(*a, **k):
        return None


_fake_data = {
    "invoices": [], "prepayments": [], "problems": [],
    "sheet_totals": {}, "sheet12_total": 0.0, "period": "2025-01",
}


def _fake_parse(path, period):
    return dict(_fake_data, period=period)


import PySide6.QtWidgets as _qt  # noqa: E402
_qt.QMessageBox = _MB
IV.QMessageBox = _MB
_imp.parse_ledger_file = _fake_parse
IV.parse_ledger_file = _fake_parse
IV.get_conn = lambda: _FakeConn()


results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}", flush=True)


# ---------------------------------------------------------------- 1) 结构
view = ImportView()
check("ImportView 是 QWidget", isinstance(view, QWidget))
check("不再是 QTabWidget", not isinstance(view, QTabWidget))
check("仍有导入日志区", hasattr(view, "log"))
check("不再持有确认面板", not hasattr(view, "_confirm_panel"))
check("已无旧 tab 引用", not hasattr(view, "_tab_confirm"))

# ---------------------------------------------------------------- 2) 台账导入 → ledger_pending 信号
got = []
view.ledger_pending.connect(lambda d, p, s, path: got.append((d, p, s, path)))
view._do_import("2025.1台账.xlsx")
check("发出 ledger_pending 信号一次", len(got) == 1, f"got={len(got)}")
if got:
    d, p, s, path = got[0]
    check("账期正确", p == "2025-01", str(p))
    check("路径正确", path == "2025.1台账.xlsx", str(path))
    check("携带职工名单(list)", isinstance(s, list))
    check("携带解析数据(period)", d.get("period") == "2025-01")
check("已无写库职责（模块不再引用 commit_ledger_import）",
      not hasattr(IV, "commit_ledger_import"))

# ---------------------------------------------------------------- 3) 非台账类型照常直导（费用打桩不必要，走解析失败分支即可）
view2 = ImportView()
msgs = []
view2.ledger_pending.connect(lambda *a: msgs.append(a))
view2._do_import("没有账期的文件.xlsx")   # 无法识别账期 → 报错不崩溃
check("无法识别账期不崩溃且不发信号", len(msgs) == 0)

# ---------------------------------------------------------------- 4) log_result 桥接
view.log_result("f.xlsx", "ledger", "2025-01", "✓ 冒烟测试导入", True)
check("log_result 写入导入日志", "✓ 冒烟测试导入" in view.log.toPlainText())

# ---------------------------------------------------------------- 5) 导入台账后自动跳到「导入复核」页（导航联动）
from app.ui.main_window import MainWindow
mw = MainWindow()
before = mw.stack.currentWidget()
mw.page_import._do_import("2025.1台账.xlsx")
after = mw.stack.currentWidget()
check("导入后自动切到复核页(review)", after is mw.page_review,
      f"before={type(before).__name__} after={type(after).__name__}")
check("复核页进入导入前模式(pre)", mw.page_review.stack.currentWidget() is mw.page_review.page_pre)

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
