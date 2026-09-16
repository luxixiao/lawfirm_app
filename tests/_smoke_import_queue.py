"""offscreen 冒烟：导入复核「待确认队列」（B 方案，2026-09-15）。

覆盖：
- 批量 open_pending ×N → 只载入队首一次（此前是每个文件 load_data 一次、
  前 N-1 个被覆盖丢失，导致未确认账期在复核页完全找不到）
- 徽章显示「第 i/N 个」+ 当前文件名 + 队列 tooltip
- 防御性不变式：open_pending 只追加、从不静默丢弃传入项（同账期两份都保留）。
  注：批量路径已由 import_view.import_folder 在导入前硬拦截同账期多文件，
  此处覆盖的是复核页自身的不变式。
- 逐个确认 → 每确认一个自动载入下一个；队列清空才回导入后模式 + navigate_back
- 批量只弹一次汇总（不再逐账期弹「导入成功」）
- 取消：队列有余量时「跳过当前」/「放弃全部待确认」/「返回」三条路径

commit_ledger_import / validate_ledger_before_write / load_data / get_conn 均打桩，
QMessageBox 换记录桩。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import app.ui.import_review_view as RV  # noqa: E402
import app.ui.review_post_view as RP  # noqa: E402
import app.engine.review_compare as RC  # noqa: E402
from app.ui.import_review_view import ImportReviewView  # noqa: E402


# ---- 打桩 ----
class _MB:
    calls = []

    class Icon:
        Question = 4
        Information = 1

    class ButtonRole:
        AcceptRole = 0
        RejectRole = 1
        DestructiveRole = 2

    def __init__(self, *a, **k):
        self._btns = []
        self._title = ""

    def setWindowTitle(self, t=""):
        self._title = t

    def setIcon(self, *a):
        pass

    def setText(self, *a):
        pass

    def setInformativeText(self, *a):
        pass

    def setDetailedText(self, *a):
        pass

    def addButton(self, label, *a):
        self._btns.append(label)
        return label

    def buttons(self):
        return list(self._btns)

    def buttonRole(self, b):
        return None

    def setDefaultButton(self, *a):
        pass

    def exec(self):
        return 0

    def clickedButton(self):
        return None

    @classmethod
    def _rec(cls, level, a):
        cls.calls.append((level, a[1] if len(a) > 1 else "",
                          a[2] if len(a) > 2 else ""))

    @classmethod
    def warning(cls, *a, **k):
        cls._rec("warning", a)

    @classmethod
    def information(cls, *a, **k):
        cls._rec("information", a)

    @classmethod
    def critical(cls, *a, **k):
        cls._rec("critical", a)

    @classmethod
    def question(cls, *a, **k):
        cls._rec("question", a)
        return 0


class _FakeCursor:
    def __iter__(self):
        return iter([])

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _FakeConn:
    def execute(self, *a, **k):
        return _FakeCursor()

    def commit(self):
        pass

    def close(self):
        pass


_commit_calls = []
_load_calls = []


def _fake_commit(data, period, path):
    _commit_calls.append((period, path))
    return {"invoice_count": 2, "prepayment_count": 1}


def _fake_load(self, data, period="", staff_names=None, path="", validator=None):
    _load_calls.append((period, path))
    self._data = data
    self._period = period
    self._path = path
    self._validate = validator


RV.QMessageBox = _MB
RP.QMessageBox = _MB
RV.commit_ledger_import = _fake_commit
RV.validate_ledger_before_write = lambda d, p: None
RV.get_conn = lambda: _FakeConn()
RC.get_conn = lambda: _FakeConn()
RV.UnifiedImportDialog.load_data = _fake_load
RV._ledger_periods = lambda: ["2025-01"]
# 待补录名单打桩：本测试只关心队列流转，不关心补录提示（否则会打到真实库）。
# 「有待补录 → 弹提示 → 去补录」的路径由 _smoke_import_review.py 覆盖。
import app.engine.backfill_module as BM  # noqa: E402
BM.list_pending_backfill = lambda conn=None: []

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}", flush=True)


def _data(period):
    return {"invoices": [], "prepayments": [], "problems": [],
            "sheet_totals": {}, "sheet12_total": 0.0, "period": period}


def _pending(v, i):
    """模拟导入页解析完第 i 个月后 emit ledger_pending。"""
    p = f"2025-{i:02d}"
    v.open_pending(_data(p), p, ["周立生"], f"2025.{i}台账.xlsx")


v = ImportReviewView()
backs = []
fin = []
v.navigate_back.connect(lambda: backs.append(1))
v.import_finished.connect(lambda f, b, p, m, ok: fin.append((f, b, p, m, ok)))

# ---------------------------------------------- 1) 批量入队：只载入队首一次
for i in range(1, 13):
    _pending(v, i)
check("12 个账期全部入队", len(v._queue) == 12, f"got={len(v._queue)}")
check("只载入队首一次（不再每文件 load_data）", len(_load_calls) == 1,
      f"got={len(_load_calls)}")
check("载入的是队首 2025-01", _load_calls[0][0] == "2025-01", str(_load_calls[:1]))
check("当前下标仍在队首", v._idx == 0)
check("队列激活", v._queue_active is True)
check("切到导入前模式", v.stack.currentWidget() is v.page_pre)
check("徽章含进度 第 1/12 个", "第 1/12 个" in v.lbl_mode.text(), v.lbl_mode.text())
check("徽章含当前账期", "2025-01" in v.lbl_mode.text(), v.lbl_mode.text())
check("徽章含当前文件名", "2025.1台账.xlsx" in v.lbl_mode.text(), v.lbl_mode.text())
check("下拉已锁定", not v.combo_period.isEnabled())
check("队列 tooltip 列出全部账期",
      "2025-01" in v.combo_period.toolTip() and "2025-12" in v.combo_period.toolTip(),
      v.combo_period.toolTip()[:60])
check("队列 tooltip 带文件名",
      "2025.1台账.xlsx" in v.combo_period.toolTip(), v.combo_period.toolTip()[:60])
check("此时尚未写库", len(_commit_calls) == 0)
check("此时未发 navigate_back", len(backs) == 0)

# ---------------------------------------------- 2) 不变式：入队只追加，不静默丢弃
v2 = ImportReviewView()
for i in range(1, 4):
    _pending(v2, i)
_load_calls.clear()
v2.open_pending(_data("2025-03"), "2025-03", ["周立生"], "2025.3台账(修正).xlsx")
check("同账期第二份文件也入队（不丢数据）", len(v2._queue) == 4,
      f"got={len(v2._queue)}")
check("入场顺序保留原名与修正名",
      [q["path"] for q in v2._queue][-2:]
      == ["2025.3台账.xlsx", "2025.3台账(修正).xlsx"],
      str([q["path"] for q in v2._queue]))
check("追加不打断当前账期（不重新载入）", len(_load_calls) == 0,
      f"got={len(_load_calls)}")
check("徽章仍停在队首 2025-01", "2025-01" in v2.lbl_mode.text(), v2.lbl_mode.text())
check("徽章进度仍为第 1/4 个", "第 1/4 个" in v2.lbl_mode.text(), v2.lbl_mode.text())

# ---------------------------------------------- 3) 逐个确认 → 自动载入下一个
_MB.calls.clear()
for i in range(1, 12):
    v._on_confirmed()
    check(f"确认第 {i} 个后载入第 {i + 1} 个",
          _load_calls[-1][0] == f"2025-{i + 1:02d}", str(_load_calls[-1]))
    check(f"确认第 {i} 个后仍在导入前模式",
          v.stack.currentWidget() is v.page_pre)
check("已确认 11 次写库", len(_commit_calls) == 11, f"got={len(_commit_calls)}")
check("写库账期顺序正确",
      [c[0] for c in _commit_calls] == [f"2025-{i:02d}" for i in range(1, 12)],
      str([c[0] for c in _commit_calls]))
check("中间过程不弹「导入成功」",
      not [c for c in _MB.calls if c[1] == "导入成功"], str(_MB.calls))

# ---- 最后一个确认 → 队列清空
v._on_confirmed()
check("第 12 个确认后回导入后模式", v.stack.currentWidget() is v.page_post)
check("队列已清空", v._queue == [] and not v._queue_active, str(v._queue))
check("共 12 次写库", len(_commit_calls) == 12, f"got={len(_commit_calls)}")
check("共发 navigate_back 一次", len(backs) == 1, f"got={len(backs)}")
check("每账期都发 import_finished", len(fin) == 12, f"got={len(fin)}")
check("import_finished 全部 ok=True", all(f[4] for f in fin), str(fin[:2]))
batch_sum = [c for c in _MB.calls if c[1] == "导入复核"]
check("批量只弹一次汇总", len(batch_sum) == 1, str(_MB.calls))
check("汇总含成功 12 个账期",
      batch_sum and "成功入库 12 个账期" in batch_sum[0][2],
      batch_sum[0][2][:50] if batch_sum else "")
check("汇总枚举各账期",
      batch_sum and "2025-01" in batch_sum[0][2] and "2025-12" in batch_sum[0][2])
check("导入后下拉重新启用", v.combo_period.isEnabled())

# ---------------------------------------------- 4) 取消：三条路径
# 4a) 跳过当前
v._queue, v._idx, v._queue_active, v._results = [], 0, False, []
_commit_calls.clear(); _load_calls.clear(); backs.clear(); _MB.calls.clear()
for i in range(1, 4):
    _pending(v, i)
v._ask_cancel = lambda period, rest: "skip"
v._on_cancelled()
check("跳过当前 → 载入下一个", _load_calls[-1][0] == "2025-02", str(_load_calls[-1]))
check("跳过当前 → 不写库", len(_commit_calls) == 0, f"got={len(_commit_calls)}")
check("跳过当前 → 仍在导入前模式", v.stack.currentWidget() is v.page_pre)
check("跳过当前 → 队列仍有 3 项（下标推进）",
      len(v._queue) == 3 and v._idx == 1, f"idx={v._idx}")

# 4b) 返回（不取消）
v._ask_cancel = lambda period, rest: "back"
v._on_cancelled()
check("返回 → 停在原账期不推进", v._idx == 1, f"idx={v._idx}")
check("返回 → 未写库", len(_commit_calls) == 0)

# 4c) 放弃全部待确认
v._ask_cancel = lambda period, rest: "all"
v._on_cancelled()
check("放弃全部 → 回导入后模式", v.stack.currentWidget() is v.page_post)
check("放弃全部 → 队列清空", v._queue == [] and not v._queue_active)
check("放弃全部 → 未写库", len(_commit_calls) == 0, f"got={len(_commit_calls)}")
check("放弃全部 → 发 navigate_back", len(backs) == 1, f"got={len(backs)}")
check("放弃全部 → 汇总弹一次",
      len([c for c in _MB.calls if c[1] == "导入复核"]) == 1, str(_MB.calls))
check("放弃全部 → 汇总标记未入库",
      "未入库 3 个" in [c for c in _MB.calls if c[1] == "导入复核"][0][2],
      [c for c in _MB.calls if c[1] == "导入复核"][0][2][:50])

# ---------------------------------------------- 5) 队尾取消：不再追问
v._queue, v._idx, v._queue_active, v._results = [], 0, False, []
_commit_calls.clear(); _load_calls.clear(); backs.clear(); _MB.calls.clear()
_pending(v, 1)
v._ask_cancel = lambda period, rest: (_ for _ in ()).throw(
    AssertionError("队尾取消不应追问"))
v._on_cancelled()
check("单账期取消不追问且静默（不弹成功框）",
      not [c for c in _MB.calls if c[1] in ("导入成功", "导入复核")], str(_MB.calls))
check("单账期取消 → 回导入后模式", v.stack.currentWidget() is v.page_post)
check("单账期取消 → 队列清空", v._queue == [] and not v._queue_active)
check("单账期取消 → 不写库", len(_commit_calls) == 0)


# ---------------------------------------------- 6) 单文件确认：沿用原提示
v._queue, v._idx, v._queue_active, v._results = [], 0, False, []
_commit_calls.clear(); _MB.calls.clear(); backs.clear()
_pending(v, 1)
v._on_confirmed()
check("单账期确认弹「导入成功」",
      len([c for c in _MB.calls if c[1] == "导入成功"]) == 1, str(_MB.calls))
check("单账期确认写库一次", len(_commit_calls) == 1)

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
