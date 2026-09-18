"""offscreen 冒烟：ImportReviewView（导入复核页，批 4 换页后）。

覆盖：
- 结构：模式徽章 + 账期下拉 + 堆栈（0=导入后 UnifiedImportDialog(mode="post")
  / 1=导入前 UnifiedImportDialog —— 批 4 起两页同为实现）
- 默认导入后模式（账期下拉启用）；open_pending 后切导入前（下拉锁定并显示该账期）
- confirmed → commit_ledger_import 一次 + navigate_back + 回导入后（下拉重新启用）
- cancelled → 不写库 + navigate_back
- 写库失败路径 → 不崩溃 + import_finished(ok=False)
- 写前校验未通过（ImportError_，阶段 3 B2d）→ 弹提示 + 留在本账期重载 + 零写入 +
  不推进队列；改好后再次确认即写库并推进
- 确认入库后「存在需补录的发票」提示：点「去补录」→ navigate_to('manual')、
  点「稍后」/无待补录 → 照旧 navigate_back
- post 页：筛选胶囊（6 项，默认待补录）/空账期安全/行级编辑回写控件初始禁用
- A5（阶段 2-1）：每个账期载入**前**重跑一次 split_deferred（判定刷新到最新库状态），
  队列追加时不刷、失败不阻断导入

post 页行为（编辑回写按钮按行解锁、还原=锚点+synced=0+有存档）的深度断言在
_smoke_review_rebuild.py C 段 —— 本文件只保初始态与构造安全。
旧 ReviewPostView（融合比对单表/标记已确认异常）已随批 4 删除。

commit_ledger_import / validate_ledger_before_write / get_conn /
list_pending_backfill 均打桩，QMessageBox 换空桩；post 页 load_period 走
review_rebuild / _uid 的模块级 get_conn（均 from app.db import get_conn）→
两个都要打桩，否则打到真实 data/lawfirm.db。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

app = QApplication.instance() or QApplication(sys.argv)

import app.ui.import_review_view as RV  # noqa: E402
import app.engine.review_compare as RC  # noqa: E402
from app.ui.import_review_view import ImportReviewView  # noqa: E402
from app.ui.unified_import_dialog import UnifiedImportDialog  # noqa: E402
from app.importer import importer as _imp  # noqa: E402


# ---- 打桩 ----
class _MBtn:
    """QMessageBox 里的一个按钮（真实 Qt 同款最小 API）。"""

    def __init__(self, text, role=None):
        self._text = text
        self.role = role

    def text(self):
        return self._text

    def setText(self, t):
        self._text = t

    def __repr__(self):
        return f"_MBtn({self._text!r}, role={self.role})"


class _MB:
    """QMessageBox 桩。

    静态方法（warning/information/critical）只记录调用；实例 API 供
    「存在需补录的发票」这类自定义按钮对话框使用：
    - setDetailedText 会像真实 Qt 一样挂一个 ActionRole 的「显示详情...」按钮
      （app 会把它改名成「查看清单」，测试据此断言中文化）；
    - click_ok=True 时 clickedButton() 返回 AcceptRole 那个按钮（「去补录」），
      否则返回 None（等价「稍后」）。
    """
    calls = []
    click_ok = False

    class Icon:
        Question = 4
        Information = 1
        Warning = 2

    class ButtonRole:
        AcceptRole = 0
        RejectRole = 1
        DestructiveRole = 2
        ActionRole = 3

    def __init__(self, *a, **k):
        self._btns = []
        self._title = ""
        _MB.last = self

    def setWindowTitle(self, t):
        self._title = t

    def setIcon(self, *a):
        pass

    def setText(self, t):
        self._text = t

    def setInformativeText(self, t):
        self._info = t

    def setDetailedText(self, t):
        self._detail = t
        self._btns.append(_MBtn("Show Details...", _MB.ButtonRole.ActionRole))

    def addButton(self, label, role=None):
        b = _MBtn(label, role)
        self._btns.append(b)
        return b

    def buttons(self):
        return list(self._btns)

    def buttonRole(self, b):
        return getattr(b, "role", None)

    def setDefaultButton(self, *a):
        pass

    def exec(self):
        _MB.calls.append(("dialog", self._title, getattr(self, "_text", ""),
                          getattr(self, "_info", "")))
        return 0

    def clickedButton(self):
        if not _MB.click_ok:
            return None
        for b in self._btns:
            if b.role == _MB.ButtonRole.AcceptRole:
                return b
        return None

    @staticmethod
    def warning(*a, **k):
        _MB.calls.append(("warning", a[1] if len(a) > 1 else "",
                          a[2] if len(a) > 2 else ""))
        return None

    @staticmethod
    def information(*a, **k):
        _MB.calls.append(("information", a[1] if len(a) > 1 else "",
                          a[2] if len(a) > 2 else ""))
        return None

    @staticmethod
    def critical(*a, **k):
        _MB.calls.append(("critical", a[1] if len(a) > 1 else "",
                          a[2] if len(a) > 2 else ""))
        return None


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


def _fake_commit(data, period, path):
    _commit_calls.append((data, period, path))
    return {"invoice_count": 2, "prepayment_count": 1}


def _fake_validate(data, period):
    return None


import PySide6.QtWidgets as _qt  # noqa: E402
from app.ui import unified_import_dialog as _uid  # noqa: E402
import app.ui.writeback_dialog as _WD  # noqa: E402
import app.engine.review_rebuild as _RR  # noqa: E402
_qt.QMessageBox = _MB
RV.QMessageBox = _MB
_uid.QMessageBox = _MB
_WD.QMessageBox = _MB
# post 页 load_period 会经这两个模块级 get_conn 查库 → 打桩隔离真实 DB
_uid.get_conn = lambda: _FakeConn()
_RR.get_conn = lambda: _FakeConn()
_imp.commit_ledger_import = _fake_commit
_imp.validate_ledger_before_write = _fake_validate
RV.commit_ledger_import = _fake_commit
RV.validate_ledger_before_write = _fake_validate
RV.get_conn = lambda: _FakeConn()
RC.get_conn = lambda: _FakeConn()
# 待补录名单打桩（否则 _prompt_backfill 会打到真实库）
import app.engine.backfill_module as BM  # noqa: E402
_pending_list = []
BM.list_pending_backfill = lambda conn=None: list(_pending_list)

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}", flush=True)


_data = {
    "invoices": [], "prepayments": [], "problems": [],
    "sheet_totals": {}, "sheet12_total": 0.0, "period": "2025-01",
}

# ---------------------------------------------------------------- 1) 结构
v = ImportReviewView()
check("ImportReviewView 是 QWidget", isinstance(v, QWidget))
check("含模式徽章与账期下拉", hasattr(v, "lbl_mode") and hasattr(v, "combo_period"))
check("堆栈 2 页", v.stack.count() == 2, f"got={v.stack.count()}")
check("导入后页 = UnifiedImportDialog(mode='post')",
      isinstance(v.page_post, UnifiedImportDialog) and v.page_post.mode == "post",
      f"{type(v.page_post).__name__}/{getattr(v.page_post, 'mode', '?')}")
check("导入前页 = UnifiedImportDialog(pre)",
      isinstance(v.page_pre, UnifiedImportDialog) and v.page_pre.mode == "pre")
check("默认导入后模式", v.stack.currentWidget() is v.page_post)
check("徽章显示已导入", "已导入" in v.lbl_mode.text(), v.lbl_mode.text())
check("导入后账期下拉启用", v.combo_period.isEnabled())
check("post 页筛选胶囊 6 项", len(v.page_post._grp.buttons()) == 6,
      str(len(v.page_post._grp.buttons())))
check("post 页默认「待补录」", v.page_post._grp.checkedId() == 0)
check("空账期安全（统计全 0 + 空表）",
      "高置信 0" in v.page_post.lbl_stat.text() and v.page_post.table.rowCount() == 0,
      f"{v.page_post.lbl_stat.text()} rows={v.page_post.table.rowCount()}")

# ---------------------------------------------------------------- 2) 导入前入口
backs = []
v.navigate_back.connect(lambda: backs.append(1))
v.open_pending(dict(_data), "2025-01", ["周立生", "陈娟"], "2025.1台账.xlsx")
check("切到导入前模式", v.stack.currentWidget() is v.page_pre)
check("徽章含账期且锁定提示", "导入前" in v.lbl_mode.text() and "2025-01" in v.lbl_mode.text(),
      v.lbl_mode.text())
check("导入前账期下拉锁定", not v.combo_period.isEnabled())
check("下拉显示锁定账期", v.combo_period.currentData() == "2025-01",
      str(v.combo_period.currentData()))
check("面板已载入数据", v.page_pre._data.get("period") == "2025-01")
check("待确认队列已入队 1 项并激活",
      len(v._queue) == 1 and v._queue_active and v._idx == 0
      and v._queue[0]["period"] == "2025-01", str(v._queue))
check("尚未写库", len(_commit_calls) == 0)

# ---------------------------------------------------------------- 3) 确认入库
fin = []
v.import_finished.connect(lambda f, b, p, m, ok: fin.append((f, b, p, m, ok)))
v.page_pre.accept()
check("确认后写库一次", len(_commit_calls) == 1, f"got={len(_commit_calls)}")
check("写库参数正确",
      _commit_calls and _commit_calls[0][1:] == ("2025-01", "2025.1台账.xlsx"),
      str(_commit_calls[:1]))
check("回导入后模式", v.stack.currentWidget() is v.page_post)
check("发 navigate_back 一次", len(backs) == 1, f"got={len(backs)}")
check("发 import_finished 且 ok=True",
      fin and fin[0][:3] == ("2025.1台账.xlsx", "ledger", "2025-01") and fin[0][4] is True,
      str(fin[:1]))
check("导入后下拉重新启用", v.combo_period.isEnabled())
check("队列已清空", v._queue == [] and not v._queue_active, str(v._queue))

# ---------------------------------------- 3b) 确认入库后「存在需补录的发票」提示
togo = []
v.navigate_to.connect(lambda k: togo.append(k))
_pending_list.clear()
_pending_list.extend([
    {"invoice_no": "MISS1", "source": "红字引用", "invoice_date": ""},
    {"invoice_no": "D100", "source": "应收账款", "invoice_date": "2024-05-06"},
])

# 3b-1) 点「稍后」→ 照旧 navigate_back
_commit_calls.clear(); backs.clear(); _MB.calls.clear(); togo.clear()
_MB.click_ok = False
v.open_pending(dict(_data), "2025-04", [], "2025.4台账.xlsx")
v.page_pre.accept()
_dlg = [c for c in _MB.calls if c[0] == "dialog" and c[1] == "存在需补录的发票"]
check("有待补录 → 弹提示一次", len(_dlg) == 1, str(_MB.calls))
check("提示含张数", _dlg and "2 张" in _dlg[0][2], _dlg[0][2] if _dlg else "")
check("提示分两类来源",
      _dlg and "红字" in _dlg[0][3] and "应收账款" in _dlg[0][3],
      _dlg[0][3] if _dlg else "")
# 弹窗按钮全中文：Qt 自动挂的「显示详情...」被改名为「查看清单」，
# 不允许出现英文按钮（历史缺陷：中间那个按钮是英文 "Show Details..."）
_btn_texts = [b.text() for b in _MB.last.buttons()]
check("弹窗按钮全中文（无英文详情按钮）",
      _btn_texts == ["查看清单", "去补录", "稍后"], str(_btn_texts))
check("点「稍后」→ 发 navigate_back、不发 navigate_to",
      len(backs) == 1 and togo == [], f"backs={len(backs)} togo={togo}")

# 3b-2) 点「去补录」→ navigate_to('manual')，不再 navigate_back
_commit_calls.clear(); backs.clear(); _MB.calls.clear(); togo.clear()
_MB.click_ok = True
v.open_pending(dict(_data), "2025-05", [], "2025.5台账.xlsx")
v.page_pre.accept()
check("点「去补录」→ 发 navigate_to('manual')", togo == ["manual"], str(togo))
check("点「去补录」→ 不再发 navigate_back", len(backs) == 0, f"got={len(backs)}")

# 3b-3) 无待补录 → 不提示
_commit_calls.clear(); backs.clear(); _MB.calls.clear(); togo.clear()
_MB.click_ok = False
_pending_list.clear()
v.open_pending(dict(_data), "2025-06", [], "2025.6台账.xlsx")
v.page_pre.accept()
check("无待补录 → 不弹提示",
      not [c for c in _MB.calls if c[0] == "dialog" and c[1] == "存在需补录的发票"],
      str(_MB.calls))
check("无待补录 → 照旧 navigate_back",
      len(backs) == 1 and togo == [], f"backs={len(backs)} togo={togo}")

# ---------------------------------------------------------------- 4) 取消
_commit_calls.clear(); backs.clear(); fin.clear()
v.open_pending(dict(_data), "2025-02", [], "2025.2台账.xlsx")
v.page_pre.reject()
check("取消后不写库", len(_commit_calls) == 0, f"got={len(_commit_calls)}")
check("取消后回导入后模式", v.stack.currentWidget() is v.page_post)
check("取消后发 navigate_back", len(backs) == 1)
check("取消后队列清空", v._queue == [] and not v._queue_active, str(v._queue))

# ---------------------------------------------------------------- 5) 写库失败路径
_commit_calls.clear(); backs.clear(); fin.clear()


def _boom(data, period, path):
    raise RuntimeError("模拟写库失败")


RV.commit_ledger_import = _boom
v.open_pending(dict(_data), "2025-03", [], "2025.3台账.xlsx")
v.page_pre.accept()
check("写库失败不崩溃", True)
check("写库失败回导入后模式", v.stack.currentWidget() is v.page_post)
check("写库失败发 navigate_back", len(backs) == 1)
check("写库失败 import_finished ok=False",
      fin and fin[0][4] is False and "模拟写库失败" in fin[0][3], str(fin[:1]))

# ---------------------------------------- 5b) 写前校验未通过 → 留在本账期返回修改（B2d）
# 弹窗点「确认入库」时已校验过一次（validator），本路径覆盖**写库前兜底**：
# commit_ledger_import 抛 ImportError_ → 弹提示 + 重载本账期 + 不推进队列 + 零写入。
from app.importer.excel_reader import ImportError_  # noqa: E402

_commit_calls.clear(); backs.clear(); fin.clear(); _MB.calls.clear()


def _reject_validate(data, period, path):
    raise ImportError_("模拟写前校验未通过：台账合计与销项不符")


RV.commit_ledger_import = _reject_validate
v.open_pending(dict(_data), "2025-07", [], "2025.7台账.xlsx")
_idx_before, _len_before = v._idx, len(v._queue)
v.page_pre.accept()
check("B2d：写前校验未通过 → 不崩溃", True)
check("B2d：弹「校验未通过」提示一次",
      len([c for c in _MB.calls if c[0] == "warning" and c[1] == "校验未通过"]) == 1,
      str(_MB.calls))
check("B2d：提示带原始错误文案",
      any("模拟写前校验未通过" in (c[2] or "")
          for c in _MB.calls if c[0] == "warning"),
      str(_MB.calls))
check("B2d：留在本账期（不推进队列、不整批重来）",
      v._idx == _idx_before and len(v._queue) == _len_before and v._queue_active,
      f"idx={v._idx} queue={len(v._queue)}")
check("B2d：仍在导入前模式（可继续就地改）",
      v.stack.currentWidget() is v.page_pre)
check("B2d：零写入 + 不发 import_finished / navigate_back",
      len(_commit_calls) == 0 and fin == [] and len(backs) == 0,
      f"fin={fin} backs={len(backs)}")
check("B2d：重载的仍是本账期",
      v.page_pre._period == "2025-07", str(v.page_pre._period))

# 改好后再确认 → 这一次写库并推进队列
RV.commit_ledger_import = _fake_commit
v.page_pre.accept()
check("B2d：修正后再次确认 → 写库一次", len(_commit_calls) == 1,
      f"got={len(_commit_calls)}")
check("B2d：修正后确认 → 队列清空回导入后模式",
      v._queue == [] and not v._queue_active
      and v.stack.currentWidget() is v.page_post)

# ------------------------------------------------ 6) post 页筛选渲染（空数据安全）
pv = v.page_post
for b in pv._grp.buttons():
    b.setChecked(True)
    b.clicked.emit()
check("六个筛选切换不崩溃", True)
pv._grp.button(_uid.FILTER_ALL).setChecked(True)
pv._render()
check("切回全部", pv._grp.checkedId() == _uid.FILTER_ALL)
pv.load_period("")   # 空账期
check("load_period 空值安全", pv.table.rowCount() == 0,
      f"rows={pv.table.rowCount()}")

# ------------------------------------------------ 7) 编辑回写控件（批 3-3，post 行级）
check("有「编辑回写」按钮且初始禁用",
      hasattr(pv, "btn_writeback") and not pv.btn_writeback.isEnabled())
check("有「还原为原件」按钮且初始禁用",
      hasattr(pv, "btn_restore") and not pv.btn_restore.isEnabled())
from app.ui.writeback_dialog import WritebackDialog  # noqa: E402
fake_raw = {"id": 1, "invoice_date_raw": "2025-01-05", "invoice_no": "X1",
            "buyer": "甲公司", "amount_raw": "100", "case_no": "",
            "remark": "", "handler_text": "张三100", "kind": "invoice"}
dlg = WritebackDialog(pv, "2025-01", "X1", fake_raw, 1)
check("WritebackDialog 可构造", dlg is not None)
check("含修改原因输入框", hasattr(dlg, "note_edit"))
check("含收款明细子表", hasattr(dlg, "tbl") and dlg.tbl.columnCount() == 3)
check("收款明细预填 0 行（get_conn 打桩）", dlg.tbl.rowCount() == 0)
dlg._append_row("2025-12", "80.00", "张三")
receipts = dlg._receipts()
check("_receipts 收集正确", receipts == [("张三", 80.0, "2025-12")], str(receipts))

# ---------------------------------------- 7b) post 页「关闭」→ 返回导入页（批 4 接线）
# post 的「关闭」走 reject() → cancelled → 宿主 _on_cancelled（队列未跑）→
# navigate_back。旧 ReviewPostView 没有「关闭」按钮，这是换页后的新行为。
_n_backs = len(backs)
pv.reject()
check("post 页「关闭」→ 发 navigate_back 一次", len(backs) == _n_backs + 1,
      f"backs={len(backs)} before={_n_backs}")
check("post 页「关闭」→ 仍停在导入后模式（队列未跑不清页面）",
      v.stack.currentWidget() is v.page_post and not v._queue_active)

# ---------------------------------------------------------------- 8) 导入前留痕装配（阶段 4）
check("collect_import_fixes 空态返回 []", v.page_pre.collect_import_fixes() == [])
# 真实时序：accept() 已把 _data 原地替换为合并结果（新值）；旧值必须取自 _orig_data
# （load_data 时的原始快照），否则 old == new → 逐字段差异被判「无变化」→ 留痕丢失
# （页面表现：旧值没有、新值只剩摘要）。
v.page_pre._orig_data = {"invoices": [
    {"invoice_no": "X1", "total_amount": 100.0, "handler_text": "张三100",
     "invoice_date": "2025-01-05", "buyer": "甲公司", "case_no": ""}]}
v.page_pre._data = {"invoices": [
    {"invoice_no": "X1", "total_amount": 200.0, "handler_text": "张三100",
     "invoice_date": "2025-01-06", "buyer": "甲公司", "case_no": ""}]}
v.page_pre._inv_edits = {0: {"invoice_no": "X1", "total_amount": 200.0,
                             "handler_text": "张三100", "buyer": "甲公司",
                             "case_no": "", "invoice_date": "2025-01-06"}}
items = v.page_pre.collect_import_fixes()
check("collect_import_fixes 产生 edit 条目",
      len(items) == 1 and items[0]["kind"] == "edit" and items[0]["invoice_no"] == "X1"
      and items[0]["old"]["total_amount"] == 100.0 and items[0]["new"]["total_amount"] == 200.0,
      str(items))

# fix 行：旧值取问题行的原始台账原文（经办人列留空 → 旧值里不出现「经办人」）
v.page_pre._orig_data = {"invoices": [], "problems": [
    {"invoice_no": "X2", "date_text": "25.1.7", "total_amount": "300",
     "handler_text": "", "buyer": "乙公司", "remark_raw": ""}]}
v.page_pre._data = {"invoices": [], "problems": []}
v.page_pre._inv_edits = {}
v.page_pre._fix = {0: {"invoice_no": "X2", "invoice_date": "2025-01-07",
                       "total_amount": 300.0, "handler_text": "李四300",
                       "buyer": "乙公司", "split_receipts": []}}
items = v.page_pre.collect_import_fixes()
check("collect_import_fixes fix 行带原始台账原文",
      len(items) == 1 and items[0]["kind"] == "fix"
      and items[0]["old"]["invoice_date"] == "25.1.7"
      and items[0]["old"]["total_amount"] == "300"
      and items[0]["old"]["handler_text"] == "",
      str(items))

# ---------------------------------------------------------------- 9) audit_view 补「字段」列（阶段 4）
import app.ui.audit_view as AV  # noqa: E402
AV.get_conn = lambda: _FakeConn()
aud = AV.AuditView()
aud._fill([])
check("修改记录页新增「字段」列", aud.table.columnCount() == 10,
      f"got={aud.table.columnCount()}")
check("字段列位于修改表名之后",
      aud.table.horizontalHeaderItem(2).text() == "字段")

# ---------------------------------------------------------------- 10) A5 判定刷新
# A5（必做）：每载入一个账期**之前**重跑一次 split_deferred（用当前库票号集合）→
# 复核页显示的待补录状态始终对应当前库状态；「批量导入 ≡ 单账期导入」由它保证。
_lib_calls, _sd_calls = [], []


def _stub_lib():
    _lib_calls.append(1)
    return {"INLIB"}


def _stub_sd(data, period, lib=None):
    _sd_calls.append((period, tuple(sorted(lib or ()))))
    return 0


RV.library_invoice_nos = _stub_lib
RV.split_deferred = _stub_sd
_lib_calls.clear(); _sd_calls.clear(); backs.clear()
v.open_pending(dict(_data), "2025-07", [], "2025.7台账.xlsx")
check("A5：载入账期前重跑 split_deferred", len(_sd_calls) == 1, str(_sd_calls))
check("A5：刷新用的是当前库票号集合",
      _sd_calls == [("2025-07", ("INLIB",))] and _lib_calls == [1],
      f"sd={_sd_calls} lib={_lib_calls}")
v.open_pending(dict(_data), "2025-08", [], "2025.8台账.xlsx")
check("A5：队列追加时不刷新（只在真正载入时刷）", len(_sd_calls) == 1, str(_sd_calls))
v.page_pre.accept()
check("A5：进到下一账期时再刷新一次",
      [c[0] for c in _sd_calls] == ["2025-07", "2025-08"], str(_sd_calls))

# 判定刷新失败不得阻断导入（仅记录告警，展示可能滞后）
# 先把上面那 2 项队列跑完（拒绝第 2 项 → 下标越界 → 队列表清空），再单独开一个队列。
v.page_pre.reject()
check("A5：队列跑完后清空（下一个队列才从队首开始）",
      v._queue == [] and not v._queue_active, str(v._queue))
_sd_calls.clear()


def _boom_lib():
    raise RuntimeError("模拟库查询失败")


RV.library_invoice_nos = _boom_lib
v.open_pending(dict(_data), "2025-09", [], "2025.9台账.xlsx")
check("A5：判定刷新失败不阻断导入（仍切到导入前模式并载入该账期）",
      v.stack.currentWidget() is v.page_pre and v.page_pre._period == "2025-09",
      f"pre={v.stack.currentWidget() is v.page_pre} period={v.page_pre._period!r}")
v.page_pre.reject()
RV.library_invoice_nos = _stub_lib

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
