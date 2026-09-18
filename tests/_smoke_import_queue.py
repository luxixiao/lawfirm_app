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
- **阶段 2-3 队列守卫三件套**（A6/A7/A8/A9）：
  · A6 离开复核页确认框（留在本页 / 确定离开 / 勾「本次不再提示」）
  · A7 `pending_count()` / `pending_periods()` 与侧栏角标 + 导入页提示条
  · A8 台账导入前置守卫（单文件拦台账、**销项照旧可导**、批量含台账则整批拒绝）
  · A9 关程序确认框（取消 / 确定关闭 / 无待确认时不弹）
- **阶段 3 B2c**：行内「补录原票」收集进 `data["backfills"]`，确认入库时随**同一次**
  commit 提交（`_commit_backfills` 断言）；「取消」/「放弃全部待确认」→ 一行不写。

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
import app.engine.review_compare as RC  # noqa: E402
import app.engine.review_rebuild as _RR  # noqa: E402
from app.ui.import_review_view import ImportReviewView  # noqa: E402
from app.ui import unified_import_dialog as _uid  # noqa: E402


# ---- 打桩 ----
class _Btn:
    """QMessageBox 按钮桩：保留文案与角色，供 clickedButton() 做同一性比较。"""

    def __init__(self, label, role=None):
        self.label = label
        self.role = role

    def text(self):
        return self.label

    def setText(self, t):
        self.label = t


class _MB:
    """QMessageBox 记录桩：exec() 不阻塞。

    - `click_label`：指定下一次 exec() 后 clickedButton() 返回的按钮文案
      （None = 返回 None，等价于「用户直接关掉」）。
    - `auto_check`：exec() 时自动勾上 setCheckBox 传入的勾选框（模拟用户勾选）。
    - `boxes`：按构造顺序记录每个框，供断言标题/正文。
    """
    calls = []
    boxes = []
    click_label = None
    auto_check = False

    class Icon:
        Question = 4
        Information = 1
        Warning = 2
        Critical = 3

    class ButtonRole:
        AcceptRole = 0
        RejectRole = 1
        DestructiveRole = 2
        ActionRole = 3

    def __init__(self, *a, **k):
        self._btns = []
        self._chk = None
        self._title = ""
        self._text = ""
        self._info = ""
        type(self).boxes.append(self)

    def setWindowTitle(self, t=""):
        self._title = t

    def setIcon(self, *a):
        pass

    def setText(self, t=""):
        self._text = t

    def setInformativeText(self, t=""):
        self._info = t

    def setDetailedText(self, *a):
        pass

    def setCheckBox(self, chk):
        self._chk = chk

    def addButton(self, label, *a):
        b = _Btn(label, a[0] if a else None)
        self._btns.append(b)
        return b

    def buttons(self):
        return list(self._btns)

    def buttonRole(self, b):
        return getattr(b, "role", None)

    def setDefaultButton(self, *a):
        pass

    def exec(self):
        if type(self).auto_check and self._chk is not None:
            self._chk.setChecked(True)
        return 0

    def clickedButton(self):
        lbl = type(self).click_label
        if lbl is None:
            return None
        for b in self._btns:
            if b.label == lbl:
                return b
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
_commit_backfills = []
_load_calls = []


def _fake_commit(data, period, path):
    _commit_calls.append((period, path))
    # 阶段 3 B2c：复核页把行内「补录原票」收集进 data["backfills"]，
    # 确认入库时随同一份 data 交给 commit_ledger_import → 这里记下来供断言。
    _commit_backfills.append(list(data.get("backfills") or []))
    return {"invoice_count": 2, "prepayment_count": 1}


def _fake_load(self, data, period="", staff_names=None, path="", validator=None):
    if getattr(self, "_mode", "") == "post":
        return  # 批 4：post 页 load_period 也走这里，不计入「队首载入」计数
    _load_calls.append((period, path))
    self._data = data
    self._period = period
    self._path = path
    self._validate = validator


RV.QMessageBox = _MB
RV.commit_ledger_import = _fake_commit
RV.validate_ledger_before_write = lambda d, p: None
RV.get_conn = lambda: _FakeConn()
RC.get_conn = lambda: _FakeConn()
# 批 4：page_post = UnifiedImportDialog(mode="post")，构造/回导入后时会走
# load_period → review_rebuild / _uid 的模块级 get_conn 查库 → 打桩隔离真实 DB。
# （load_data 本身已被下方类级桩替换，但 load_period 在其之前还有两次查库。）
_uid.get_conn = lambda: _FakeConn()
_RR.get_conn = lambda: _FakeConn()
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

# ---------------------------------------------- 7) A6/A7/A8：队列状态查询接口
v._queue, v._idx, v._queue_active, v._results = [], 0, False, []
_commit_calls.clear(); _load_calls.clear(); backs.clear(); _MB.calls.clear()
_MB.boxes.clear(); _MB.click_label = None; _MB.auto_check = False
qc = []
v.queue_changed.connect(lambda: qc.append(1))

check("A7 无队列时 pending_count=0", v.pending_count() == 0, f"got={v.pending_count()}")
check("A7 无队列时 pending_periods=[]", v.pending_periods() == [], str(v.pending_periods()))
check("A8 无队列时不拦（None）", v.blocking_message() is None,
      str(v.blocking_message()))
check("A6 无队列时允许离开且不弹框", v.confirm_leave() is True and not _MB.boxes,
      str([b._title for b in _MB.boxes]))

for i in (1, 2, 3):
    _pending(v, i)
check("A7 入队 3 个 → pending_count=3", v.pending_count() == 3,
      f"got={v.pending_count()}")
check("A7 pending_periods 按队列顺序",
      v.pending_periods() == ["2025-01", "2025-02", "2025-03"],
      str(v.pending_periods()))
check("A7 每次队列变化都发 queue_changed", len(qc) == 3, f"got={len(qc)}")
_msg = v.blocking_message()
check("A8 文案列出全部未确认账期",
      _msg is not None and all(p in _msg for p in ("2025-01", "2025-02", "2025-03")),
      str(_msg)[:80])
check("A8 文案含处置指引（确认入库 / 放弃全部待确认）",
      _msg is not None and "确认入库" in _msg and "放弃全部待确认" in _msg,
      str(_msg)[:80])

v._on_confirmed()
check("A7 推进一个 → pending_count=2", v.pending_count() == 2,
      f"got={v.pending_count()}")
check("A7 推进后账期去掉 2025-01",
      v.pending_periods() == ["2025-02", "2025-03"], str(v.pending_periods()))
check("A7 推进也发 queue_changed", len(qc) == 4, f"got={len(qc)}")

# A6 离开确认：三条路径
_MB.boxes.clear()
_MB.click_label = "留在本页"
check("A6 选「留在本页」→ 不允许离开", v.confirm_leave() is False)
check("A6 弹一次确认框", len(_MB.boxes) == 1, str([b._title for b in _MB.boxes]))
check("A6 文案含未确认个数与「随时回到本页」",
      _MB.boxes and "2 个账期待确认入库" in _MB.boxes[0]._text
      and "随时回到本页" in _MB.boxes[0]._text,
      _MB.boxes[0]._text if _MB.boxes else "")
check("A6 提供「本次不再提示」勾选框",
      _MB.boxes and _MB.boxes[0]._chk is not None)
_MB.click_label = "确定离开"
check("A6 选「确定离开」→ 允许离开", v.confirm_leave() is True)
check("A6 未勾选时不会静默放行",
      v._leave_no_prompt is False and v.confirm_leave() is True)
check("A6 未勾选时每次离开都弹", len(_MB.boxes) == 3, f"got={len(_MB.boxes)}")

# A6 勾「本次不再提示」→ 本队列内不再打扰；新队列重新武装
_MB.boxes.clear()
_MB.auto_check = True
_MB.click_label = "确定离开"
v.confirm_leave()
check("A6 勾选后标记不再提示", v._leave_no_prompt is True)
_MB.boxes.clear()
check("A6 勾选后不再弹框且允许离开",
      v.confirm_leave() is True and not _MB.boxes, str([b._title for b in _MB.boxes]))
_MB.auto_check = False
v._queue, v._idx, v._queue_active, v._results = [], 0, False, []
_pending(v, 1)
check("A6 新队列重新武装提示", v._leave_no_prompt is False)
v._queue, v._idx, v._queue_active, v._results = [], 0, False, []
_MB.click_label = None

# ---------------------------------------------- 8) A8：台账导入前置守卫（ImportView 层）
import tempfile  # noqa: E402
import shutil  # noqa: E402
import app.ui.import_view as _IV  # noqa: E402

_IV.QMessageBox = _MB
_IV.get_conn = lambda: _FakeConn()

_GUARD = ("存在未确认入库的账期（2025-01、2025-02），请先完成「确认入库」，"
          "或点「取消 → 放弃全部待确认」清空后再导入新的发票台账。")


class _FD2:
    """QFileDialog 桩：单文件 / 文件夹路径可分别指定。"""
    path = "2025.1台账.xlsx"
    folder = ""

    @staticmethod
    def getOpenFileName(*a, **k):
        return (_FD2.path, "")

    @staticmethod
    def getExistingDirectory(*a, **k):
        return _FD2.folder


_IV.QFileDialog = _FD2


def _mk_iv(guard, pending=0):
    """造一个 ImportView，`_do_import` 换成记录桩（不碰真实文件/库）。"""
    w = _IV.ImportView()
    seen = []
    w._do_import = lambda path, quiet=False: seen.append(path)
    w.ledger_guard = (lambda: guard) if guard is not None else None
    w.pending_count_fn = (lambda: pending) if pending is not None else None
    return w, seen


# 8a) 未接线（单测 / 旧路径）→ 不拦
_FD2.path = "2025.1台账.xlsx"
_vw, _seen = _mk_iv(None)
_vw.import_file()
check("A8 未接线时不拦台账", _seen == ["2025.1台账.xlsx"], str(_seen))

# 8b) 队列未跑完 + 台账 → 拦在解析/入队之前
_MB.calls.clear()
_vw, _seen = _mk_iv(_GUARD)
_vw.import_file()
check("A8 队列未跑完 → 台账被拦（不解析、不入队）", _seen == [], str(_seen))
_warn = [c for c in _MB.calls if c[1] == "存在未确认入库的账期"]
check("A8 弹一次中文拦截提示", len(_warn) == 1, str(_MB.calls))
check("A8 提示含未确认账期 + 处置指引",
      _warn and all(k in _warn[0][2] for k in
                    ("2025-01", "2025-02", "确认入库", "放弃全部待确认")),
      _warn[0][2][:100] if _warn else "")
check("A8 拦截写入导入日志", "已中止导入" in _vw.log.toPlainText(),
      _vw.log.toPlainText()[-70:])

# 8c) 队列未跑完 + 非台账 → 照旧可导（硬边界，必须写死）
_MB.calls.clear()
_vw, _seen = _mk_iv(_GUARD)
_FD2.path = "2025.1销项.xlsx"
_vw.import_file()
check("A8 销项文档不受守卫影响（保住「先导销项」顺序依赖）",
      _seen == ["2025.1销项.xlsx"], str(_seen))
check("A8 销项未被拦（不弹拦截提示）",
      not [c for c in _MB.calls if c[1] == "存在未确认入库的账期"], str(_MB.calls))
_FD2.path = "2025.1费用.xlsx"
_vw.import_file()
check("A8 费用台账不受守卫影响", _seen[-1] == "2025.1费用.xlsx", str(_seen))
_FD2.path = "职工清单.xlsx"
_vw.import_file()
check("A8 职工清单不受守卫影响", _seen[-1] == "职工清单.xlsx", str(_seen))
_FD2.path = "2025.1台账.xlsx"

# 8d) 守卫返回 None（队列已清空）→ 台账放行
_vw, _seen = _mk_iv(None)
_vw.import_file()
check("A8 队列已清空 → 台账放行", _seen == ["2025.1台账.xlsx"], str(_seen))

# 8e/8f) 批量：含台账 → 整批拒绝；纯非台账 → 照旧处理
_t_led = tempfile.mkdtemp(prefix="lawfirm_q_guard_led_")
_t_inv = tempfile.mkdtemp(prefix="lawfirm_q_guard_inv_")
try:
    for _n in ["2025.1销项.xlsx", "2025.1台账.xlsx", "2025.2费用.xlsx"]:
        with open(os.path.join(_t_led, _n), "w"):
            pass
    for _n in ["2025.1销项.xlsx", "2025.2费用.xlsx", "25.1工资.xlsx"]:
        with open(os.path.join(_t_inv, _n), "w"):
            pass

    _MB.calls.clear()
    _FD2.folder = _t_led
    _vw, _seen = _mk_iv(_GUARD)
    _vw.import_folder()
    check("A8 批量含台账 → 整批拒绝（不处理任何文件，避免半成品批次）",
          _seen == [], str(_seen))
    _warn = [c for c in _MB.calls if c[1] == "存在未确认入库的账期"]
    check("A8 批量拒绝弹一次提示", len(_warn) == 1, str(_MB.calls))
    check("A8 批量提示说明未写入任何数据",
          _warn and "未写入任何数据" in _warn[0][2],
          _warn[0][2][:120] if _warn else "")
    check("A8 批量拒绝不弹成功汇总",
          not [c for c in _MB.calls if c[1] == "批量导入"], str(_MB.calls))
    check("A8 批量拒绝写入导入日志", "已中止" in _vw.log.toPlainText())

    _MB.calls.clear()
    _FD2.folder = _t_inv
    _vw, _seen = _mk_iv(_GUARD)
    _vw.import_folder()
    check("A8 文件夹无台账 → 队列未跑完也照旧可导",
          sorted(os.path.basename(p) for p in _seen)
          == ["2025.1销项.xlsx", "2025.2费用.xlsx", "25.1工资.xlsx"], str(_seen))
    check("A8 纯非台账批量照常弹成功汇总",
          len([c for c in _MB.calls if c[1] == "批量导入"]) == 1, str(_MB.calls))
finally:
    shutil.rmtree(_t_led, ignore_errors=True)
    shutil.rmtree(_t_inv, ignore_errors=True)

# ---------------------------------------------- 9) A6/A7/A9：主窗口接线（端到端）
import app.ui.main_window as _MW  # noqa: E402

_MW.QMessageBox = _MB
_mw = _MW.MainWindow()
_badge = _mw.sidebar._item_buttons["review"]
check("A7 角标落在「导入复核」子项上（key=review）",
      _badge.objectName() == "navItem" and hasattr(_badge, "set_badge"))
check("A7 初始无待确认 → 角标为空", _badge.badge() == "", repr(_badge.badge()))
check("A7 初始 → 导入页提示条隐藏", _mw.page_import.banner_pending.isHidden())
check("A8 守卫已接线到导入页",
      _mw.page_import.ledger_guard is not None
      and _mw.page_import.pending_count_fn is not None)

_mw.page_import.ledger_pending.emit(_data("2025-01"), "2025-01", ["周立生"],
                                    "2025.1台账.xlsx")
check("A7 入队 → 侧栏角标 = 1", _badge.badge() == "1", repr(_badge.badge()))
check("A7 入队 → 提示条可见", not _mw.page_import.banner_pending.isHidden())
check("A7 提示条文案含个数与「去处理」",
      "1" in _mw.page_import.banner_pending.text()
      and "去处理" in _mw.page_import.banner_pending.text(),
      _mw.page_import.banner_pending.text())

_mw.page_import.ledger_pending.emit(_data("2025-02"), "2025-02", ["周立生"],
                                    "2025.2台账.xlsx")
check("A7 追加 → 角标 = 2", _badge.badge() == "2", repr(_badge.badge()))
_guard_msg = _mw.page_import._ledger_block_message()
check("A8 主窗口链路守卫文案已生效（含未确认账期）",
      _guard_msg is not None and "2025-01" in _guard_msg, str(_guard_msg)[:70])

# A6：离开复核页
_mw.select("review")
check("A6 切到复核页本身不弹确认", _mw.stack.currentWidget() is _mw.page_review)
_MB.boxes.clear()
_MB.click_label = "留在本页"
_mw.select("import")
check("A6 队列未跑完 + 选「留在本页」→ 停在复核页",
      _mw.stack.currentWidget() is _mw.page_review)
check("A6 弹一次离开确认框", len(_MB.boxes) == 1,
      str([b._title for b in _MB.boxes]))
check("A6 文案含个数与「随时回到本页」",
      _MB.boxes and "2 个账期待确认入库" in _MB.boxes[0]._text
      and "随时回到本页" in _MB.boxes[0]._text,
      _MB.boxes[0]._text if _MB.boxes else "")
_MB.click_label = "确定离开"
_mw.select("import")
check("A6 选「确定离开」→ 切到导入页",
      _mw.stack.currentWidget() is _mw.page_import)
check("A6 离开后队列仍在（不丢已解析数据）",
      _mw.page_review.pending_count() == 2, f"got={_mw.page_review.pending_count()}")

# A9：关程序确认
_MB.boxes.clear()
_MB.click_label = "取消"
check("A9 队列未跑完 + 选「取消」→ 不允许关闭",
      _mw._confirm_close_with_pending() is False)
check("A9 文案含「重新导入」与「不会写入任何数据」",
      _MB.boxes and "重新导入" in _MB.boxes[0]._text
      and "不会写入任何数据" in _MB.boxes[0]._text,
      _MB.boxes[0]._text if _MB.boxes else "")
check("A9 文案含未确认个数",
      _MB.boxes and "2 个账期待确认入库" in _MB.boxes[0]._text,
      _MB.boxes[0]._text if _MB.boxes else "")
_MB.click_label = "确定关闭"
check("A9 选「确定关闭」→ 允许关闭", _mw._confirm_close_with_pending() is True)

# 队列跑完 → 角标/提示条自动清空，A6/A9 不再打扰
_mw.page_review._queue, _mw.page_review._idx = [], 0
_mw.page_review._queue_active = False
_mw._refresh_pending_ui()
check("A7 队列清空 → 角标清空", _badge.badge() == "", repr(_badge.badge()))
check("A7 队列清空 → 提示条隐藏", _mw.page_import.banner_pending.isHidden())
_MB.boxes.clear()
_MB.click_label = None
_mw.select("review")
_mw.select("import")
check("A6 无待确认 → 不弹框", not _MB.boxes, str([b._title for b in _MB.boxes]))
check("A9 无待确认 → 直接放行且不弹框",
      _mw._confirm_close_with_pending() is True and not _MB.boxes,
      str([b._title for b in _MB.boxes]))


# ---------------------------------------------- 10) 阶段3 B2c：行内补录随台账入库 / 取消零写库
v._queue, v._idx, v._queue_active, v._results = [], 0, False, []
_commit_calls.clear(); _commit_backfills.clear(); _load_calls.clear()
backs.clear(); _MB.calls.clear(); _MB.click_label = None

BF = [{"invoice_no": "BF-1", "invoice_date": "2024-01-01", "buyer": "某某公司",
       "total_amount": 1000.0,
       "handlers": [{"name": "周立生", "billing": 1000.0,
                     "received": 0.0, "date": ""}],
       "create": True}]

# 10a) 确认入库：data 携带的行内补录随**同一次** commit 提交
_d_bf = _data("2025-01")
_d_bf["backfills"] = [dict(BF[0])]
v.open_pending(_d_bf, "2025-01", ["周立生"], "2025.1台账.xlsx")
check("B2c：队列项被载入且携带 backfills（面板可读）",
      (v.page_pre._data.get("backfills") or []) == BF,
      str(v.page_pre._data.get("backfills")))
v._on_confirmed()
check("B2c：确认入库 → 写库数据携带行内补录",
      _commit_backfills and _commit_backfills[-1] == BF,
      str(_commit_backfills[-1:]))
check("B2c：补录与台账是同一次写库（只 commit 一次）",
      len(_commit_calls) == 1, f"got={len(_commit_calls)}")
check("B2c：确认后回导入后模式",
      v._queue == [] and not v._queue_active
      and v.stack.currentWidget() is v.page_post)

# 10b) 取消当前账期（队尾）→ 一行不写
_commit_calls.clear(); _commit_backfills.clear()
_d_bf2 = _data("2025-02")
_d_bf2["backfills"] = [dict(BF[0])]
v.open_pending(_d_bf2, "2025-02", ["周立生"], "2025.2台账.xlsx")
v._ask_cancel = lambda period, rest: (_ for _ in ()).throw(
    AssertionError("队尾取消不应追问"))
v._on_cancelled()
check("B2c：取消当前账期 → 一行未写（含行内补录）",
      len(_commit_calls) == 0 and len(_commit_backfills) == 0,
      f"commit={_commit_calls} bf={_commit_backfills}")
check("B2c：取消后回导入后模式且队列清空",
      v._queue == [] and not v._queue_active
      and v.stack.currentWidget() is v.page_post)

# 10c) 放弃全部待确认 → 每份带补录的账期都不写库
_commit_calls.clear(); _commit_backfills.clear()
for i in (3, 4):
    _dd = _data(f"2025-{i:02d}")
    _dd["backfills"] = [dict(BF[0])]
    v.open_pending(_dd, f"2025-{i:02d}", ["周立生"], f"2025.{i}台账.xlsx")
check("B2c：两份带补录的账期都已入队", len(v._queue) == 2, f"got={len(v._queue)}")
v._ask_cancel = lambda period, rest: "all"
v._on_cancelled()
check("B2c：放弃全部待确认 → 两份账期都不写库（补录一并不写）",
      len(_commit_calls) == 0 and len(_commit_backfills) == 0,
      f"commit={_commit_calls} bf={_commit_backfills}")
check("B2c：放弃全部后回导入后模式",
      v._queue == [] and not v._queue_active
      and v.stack.currentWidget() is v.page_post)


bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
