"""offscreen 冒烟：ImportView（导入复核页改造 阶段 1 后）。

覆盖：
- ImportView 退回 QWidget（原 QTabWidget 的「导入确认」tab 已移除）
- 发票台账导入解析后通过 ledger_pending 信号交给「导入复核」页，本页不写库
- log_result 桥接导入复核页回传的结果（写导入日志）
- ledger_queue_ready：单文件/批量处理完后只切一次页
- 批量：同类型同账期多文件 → 写库前硬拦截中止；无冲突则按账期升序入队

为不碰真实文件/数据库，parse_ledger_file / import_invoice_file 打桩、get_conn 用假连接。
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
    """offscreen 下 QMessageBox 会阻塞，用记录桩替代（info_calls 收 (标题, 正文)）。"""
    info_calls = []

    @staticmethod
    def warning(*a, **k):
        return None

    @staticmethod
    def question(*a, **k):
        return 1

    @staticmethod
    def _rec(a):
        _MB.info_calls.append((a[1] if len(a) > 1 else "",
                               a[2] if len(a) > 2 else ""))

    @staticmethod
    def information(*a, **k):
        _MB._rec(a)
        return None

    @staticmethod
    def critical(*a, **k):
        _MB._rec(a)
        return None


_fake_data = {
    # 必须非空：_do_import 的 ledger 分支对「解析结果为空」会直接报错
    "invoices": [{"invoice_no": "X1", "total_amount": 100.0,
                  "invoice_date": "2025-01-05", "buyer": "甲公司",
                  "handlers": [("周立生", 100.0)], "handler_text": "周立生100",
                  "remark": {}, "sheet_name": "开票明细", "row_no": 2}],
    "prepayments": [], "problems": [],
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
# 销项文档也打桩：批量用例里混入了 2025.1销项.xlsx，不能让测试写真实库
IV.import_invoice_file = lambda path, period: {"count": 3}


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
ret = view._do_import("2025.1台账.xlsx")
check("发出 ledger_pending 信号一次", len(got) == 1, f"got={len(got)}")
check("台账分支返回 'ledger'（交由待确认队列）", ret == "ledger", str(ret))
if got:
    d, p, s, path = got[0]
    check("账期正确", p == "2025-01", str(p))
    check("路径正确", path == "2025.1台账.xlsx", str(path))
    check("携带职工名单(list)", isinstance(s, list))
    check("携带解析数据(period)", d.get("period") == "2025-01")
check("已无写库职责（模块不再引用 commit_ledger_import）",
      not hasattr(IV, "commit_ledger_import"))

# ---------------------------------------------------------------- 2b) ledger_queue_ready（单文件）
class _FD:
    """QFileDialog 桩：单文件选 2025.1台账.xlsx。"""
    path = "2025.1台账.xlsx"

    @staticmethod
    def getOpenFileName(*a, **k):
        return (_FD.path, "")

    @staticmethod
    def getExistingDirectory(*a, **k):
        return ""


IV.QFileDialog = _FD
ready = []
view.ledger_queue_ready.connect(lambda: ready.append(1))
view.import_file()
check("单文件导入后发 ledger_queue_ready 一次", len(ready) == 1, f"got={len(ready)}")
_FD.path = ""                      # 用户取消选择文件
view.import_file()
check("取消选择文件时不发 ledger_queue_ready", len(ready) == 1, f"got={len(ready)}")
_FD.path = "2025.1台账.xlsx"

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
mw.page_import.import_file()          # 走真实单文件路径（QFileDialog 已打桩）
after = mw.stack.currentWidget()
check("导入后自动切到复核页(review)", after is mw.page_review,
      f"before={type(before).__name__} after={type(after).__name__}")
check("复核页进入导入前模式(pre)", mw.page_review.stack.currentWidget() is mw.page_review.page_pre)
check("复核页待确认队列已激活", mw.page_review._queue_active is True)

# ---------------------------------------------------------------- 6) 批量导入：同账期多文件硬拦截 + 按账期排序
import tempfile  # noqa: E402
import shutil  # noqa: E402

_t_bad = tempfile.mkdtemp(prefix="lawfirm_batch_conflict_")
_t_ok = tempfile.mkdtemp(prefix="lawfirm_batch_ok_")
try:
    # 冲突夹：2025-03 有两份台账
    for n in ["2025.10台账.xlsx", "2025.2台账.xlsx", "2025.1台账.xls",
              "2025.3台账.xlsx", "2025.3台账(修正).xlsx",
              "2025.11台账.xlsx", "2025.12台账.xlsx"]:
        with open(os.path.join(_t_bad, n), "w"):
            pass
    # 干净夹：账期不重复；另放一份同账期的销项文档（不同类型，不应误判冲突）
    for n in ["2025.10台账.xlsx", "2025.2台账.xlsx", "2025.1台账.xls",
              "2025.3台账.xlsx", "2025.11台账.xlsx", "2025.12台账.xlsx",
              "2025.1销项.xlsx"]:
        with open(os.path.join(_t_ok, n), "w"):
            pass


    def _fd_for(d):
        class _F(_FD):
            @staticmethod
            def getExistingDirectory(*a, **k):
                return d

        return _F


    # 6a) 同账期多文件 → 写库/入队之前就中止
    IV.QFileDialog = _fd_for(_t_bad)
    v_bad = ImportView()
    got_bad, ready_bad = [], []
    v_bad.ledger_pending.connect(lambda *a: got_bad.append(a))
    v_bad.ledger_queue_ready.connect(lambda: ready_bad.append(1))
    _MB.info_calls.clear()
    v_bad.import_folder()
    check("同账期多文件 → 不导入任何文件（不入队、不切页）",
          got_bad == [] and ready_bad == [],
          f"pending={len(got_bad)} ready={len(ready_bad)}")
    crit = [t for ti, t in _MB.info_calls if ti == "批量导入已中止"]
    check("同账期多文件 → 弹一次中文中止提示", len(crit) == 1, str(_MB.info_calls))
    check("中止提示含冲突账期 + 两份文件名 + 类型",
          crit and all(k in crit[0] for k in
                       ("2025-03", "2025.3台账.xlsx",
                        "2025.3台账(修正).xlsx", "发票台账")),
          crit[0][:140] if crit else "")
    check("中止提示含「重新导入」指引",
          crit and "重新导入" in crit[0] and "未写入任何数据" in crit[0])
    check("同账期多文件 → 不弹成功汇总",
          not [t for ti, t in _MB.info_calls if ti == "批量导入"])
    check("同账期多文件 → 写中止日志", "已中止" in v_bad.log.toPlainText())

    # 6b) 无冲突 → 正常导入，按账期升序（2025.10 不再排到 2025.2 之前）
    IV.QFileDialog = _fd_for(_t_ok)
    v_ok = ImportView()
    order, ready_ok = [], []
    v_ok.ledger_pending.connect(
        lambda d, p, s, path: order.append((p, os.path.basename(path))))
    v_ok.ledger_queue_ready.connect(lambda: ready_ok.append(1))
    _MB.info_calls.clear()
    v_ok.import_folder()
    periods = [p for p, _n in order]
    check("无冲突 → 台账按账期升序入队",
          periods == ["2025-01", "2025-02", "2025-03",
                      "2025-10", "2025-11", "2025-12"], str(periods))
    check("无冲突 → 2025.10 排在 2025.2 之后",
          periods.index("2025-10") > periods.index("2025-02"), str(periods))
    check("无冲突 → 只发一次 ledger_queue_ready", len(ready_ok) == 1,
          f"got={len(ready_ok)}")
    batch_msg = [t for ti, t in _MB.info_calls if ti == "批量导入"]
    check("无冲突 → 弹一次成功汇总", len(batch_msg) == 1, str(batch_msg))
    check("无冲突 → 汇总含 7 个成功文件",
          batch_msg and "成功 7 个" in batch_msg[0],
          batch_msg[0].splitlines()[0] if batch_msg else "")
    check("不同类型同账期不误判冲突",
          "已中止" not in v_ok.log.toPlainText())
    check("销项文档与台账同账期各自导入",
          "2025.1销项.xlsx" in v_ok.log.toPlainText())
finally:
    shutil.rmtree(_t_bad, ignore_errors=True)
    shutil.rmtree(_t_ok, ignore_errors=True)

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
