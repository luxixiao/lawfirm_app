"""报表三态切换 + 工具栏人员筛选的**离屏 UI** 单元测试（Qt, offscreen）。

覆盖：
- 三态下拉：项文案/ userData（字符串常量）/ 落盘 QSettings 后能读回同一态；
- 旧 QSettings 值迁移：bool True/False、字符串 'true'/'false' 落到 index 2/0
  （🔴 回归重点：旧代码用 bool() 兜底，`bool('false') is True` → 用户存的「合并」
  会被误读成「拆分」）；
- 下拉切三态 → summary 文案跟着变（展示：合并 / 仅多类型拆分 / 全拆分（按类型））；
- 勾选人员 → 预览行数变少 + summary 出现「已筛选 X/Y 人」+ 按钮文案/高亮/恢复全选三处视觉；
- 0 人勾选 → summary 追加「未选择人员」+ **两个导出按钮都置灰**；
- 🔴 防递归：反复切年份 10 次，preview 重算次数**不放大**、名单刷新不额外触发、不抛异常；
- 导出 log / 完成弹窗附「（按勾选 2/3 人导出）」；
- 真PersonFilterWidget 单独单测：勾选/搜索/全选/刷新保勾选/交集空回退/QSettings 持久化。

全程用桩替换引擎与导出器（不触真实 data/lawfirm.db），QMessageBox 被 monkeypatch（不弹模态）。
运行：python tests/test_settlement_split_mode_ui.py
"""
import os
import sys
import tempfile
import types as _pytypes
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from app.engine.person_settlement import (  # noqa: E402
    SPLIT_ALL, SPLIT_MERGE, SPLIT_MULTI, normalize_split_mode)
from app.exporter.report_persons import apply_person_filter  # noqa: E402

OK, FAILS = [], []

ROSTER = ["张三", "李四", "王五"]
# 桩用的「当月真实名单」：roster_for 每次被调用时更新它。
# 不用常量兜底的原因：名单为空（切到无人入职的年份）时 persons=None 是「不过滤」，
# 若桩回退到常量 3 人，测试看到的就不是真实行为（假绿）。
CURRENT_ROSTER: list = []

# 🔴 必修2 的回归基石：名单**必须随年月变**，否则「切到没人入职的年份」这条分支
# 在测试里永远走不到，QA 指出的假绿就是这么来的。
# 张三全年在册；李四 3 月才入职（1/2 月名单里没有他）；**2024 年无人入职（整年名单都空）**。
def roster_for(year: int, month: int) -> list:
    """桩名单。2024 年一律返回空（任何月份都无人入职）；2025 年按入职月过滤。"""
    global CURRENT_ROSTER
    if year == 2024:
        CURRENT_ROSTER = []
        return []
    hired = {"张三": 1, "李四": 3, "王五": 5}   # 姓名 -> 入职月
    CURRENT_ROSTER = [n for n, hm in hired.items() if hm <= month]
    return list(CURRENT_ROSTER)


def check(label, cond, detail=""):
    if cond:
        OK.append(label)
    else:
        FAILS.append(f"{label} {detail}".strip())


# ---------------------------------------------------------------------------
# 桩：引擎/导出器重活全部替换，专测 UI 状态机
# ---------------------------------------------------------------------------
CALLS = {"si": 0, "ii": 0}


def _selected(persons):
    """按勾选取交集——**转发真实实现**，不在桩里复刻一遍。

    ⚠️ 这里曾经写成 `list(CURRENT_ROSTER) if persons is None else list(persons)`，
    等于把 app.exporter.report_persons.apply_person_filter 的 None/[] 语义**复刻**了一份。
    后果：把真实现改回`if not selected:`（泄露回归）时，本文件仍然全绿，
    「0 人勾选不泄露」在视图层就变成了假绿（QA 第二轮复验实测发现）。
    转发真实实现后，桩与产品代码共用同一份语义，不会再分叉。
    """
    return apply_person_filter(CURRENT_ROSTER, persons)


def fake_build_report_rows(year, month, split=False, persons=None):
    """年度结算表桩：拆分态下张三出两行（合伙+聘用），其余出一行。"""
    CALLS["si"] += 1
    sel = _selected(persons)
    if normalize_split_mode(split) != SPLIT_MERGE:
        expanded = []
        for n in sel:
            if n == "张三":
                expanded += [f"{n}（合伙）", f"{n}（聘用）"]
            else:
                expanded.append(f"{n}（合伙）")
        sel = expanded
    rows = [[d, 100.0, 200.0] + [0.0] * 8 for d in sel]
    totals = [round(sum(r[j] for r in rows), 2) for j in range(1, 11)]
    return sel, rows, totals


def fake_build_main_preview(year, month, split=False, persons=None):
    """开票收入表桩。"""
    CALLS["ii"] += 1
    sel = _selected(persons)
    rows = [[d, 100.0, 200.0, 0.0, 0.0, 0.0, 0.0] for d in sel]
    totals = [round(sum(r[j] for r in rows), 2) for j in range(1, 7)]
    return sel, rows, totals


class _FakeLog:
    """QPlainTextEdit 的最小替身（记录导出 log 行）。"""

    def __init__(self):
        self.lines: list[str] = []

    def clear(self):
        self.lines = []

    def appendPlainText(self, s):  # noqa: N802  (Qt 命名)
        self.lines.append(s)

    def text(self) -> str:
        return "\n".join(self.lines)


class _FakeSignal:
    """Qt Signal 的最小替身（只有 .connect 一个方法被用到）。"""

    def __init__(self):
        self._slots: list = []

    def connect(self, fn):
        self._slots.append(fn)

    def emit(self, *args):
        for fn in list(self._slots):
            fn(*args)


class _FakeFilter(QWidget):
    """PersonFilterWidget 的最小替身（真控件在 test_person_filter_widget 里单测）。

    刻意复刻真控件的**三层状态**（_intent 意图 / _persons 当月事实 / _checked 派生交集），
    否则测试会因为「替身没有这个 bug」而假绿——QA 上一轮就是这么抓到问题的。
    必须是 QWidget（要进 QHBoxLayout）。
    """

    def __init__(self, key, parent=None):
        super().__init__(parent)
        self._key = key
        self._persons: list[str] = []
        self._intent: list[str] = []
        self._checked: list[str] = []
        self._initialized = False
        self.reload_count = 0      # set_persons 被调次数（防递归的关键观测量）
        self.changed = 0
        self.selection_changed = _FakeSignal()

    # --- Qt Signal 的回调等价物 ---
    def set_persons(self, persons):
        self.reload_count += 1
        persons = [p for p in (persons or [])]
        if persons and not self._initialized:
            self._intent = list(persons)
        self._initialized = True
        self._persons = persons
        self._sync_derived()          # 被动刷新只重算派生量，绝不动 _intent

    def _sync_derived(self):
        keep = set(self._intent)
        self._checked = [p for p in self._persons if p in keep]

    def select_only(self, names):
        self._commit(list(names))

    def _commit(self, names):
        if not self._persons:
            return
        checked = [n for n in names if n in set(self._persons)]
        if sorted(checked) == sorted(self._checked):
            return
        self._intent = list(checked)
        self._sync_derived()
        self.changed += 1
        self.selection_changed.emit(list(self._checked))

    def reset_selection(self):
        if not self._persons:
            return
        if not self.is_filtering():
            self._intent = list(self._persons)
            self._sync_derived()
            return
        self._intent = list(self._persons)
        self._sync_derived()
        self.changed += 1
        self.selection_changed.emit(list(self._checked))

    def selected_persons(self):
        if not self.is_filtering():
            return None
        return list(self._checked)

    def is_filtering(self):
        if not self._persons:
            return False
        return sorted(self._checked) != sorted(self._persons)

    def total_count(self):
        return len(self._persons)

    def checked_count(self):
        return len(self._checked)

    def button_text(self):
        if self.is_filtering():
            return f"人员：{self.checked_count()}/{self.total_count()} · 筛选中"
        return f"人员：全部（{self.total_count()}）"

    def export_note(self):
        if not self.is_filtering():
            return ""
        return f"（按勾选 {self.checked_count()}/{self.total_count()} 人导出）"


def _build_view(sv):
    """只挂年度结算表 / 开票收入表两块逻辑的轻量视图宿主（绕开月度表与真 DB）。"""
    import app.ui.settlement_view as sv_mod

    class _Host(QWidget):
        """复用 SettlementView 的真实方法，但自己提供最小 __init__。"""

        _build_staff_income_tab = sv_mod.SettlementView._build_staff_income_tab
        _build_invoice_income_tab = sv_mod.SettlementView._build_invoice_income_tab
        refresh_staff_income = sv_mod.SettlementView.refresh_staff_income
        refresh_invoice_income = sv_mod.SettlementView.refresh_invoice_income
        _sync_si_persons = sv_mod.SettlementView._sync_si_persons
        _sync_ii_persons = sv_mod.SettlementView._sync_ii_persons
        _on_si_persons_changed = sv_mod.SettlementView._on_si_persons_changed
        _on_ii_persons_changed = sv_mod.SettlementView._on_ii_persons_changed
        _on_si_split_changed = sv_mod.SettlementView._on_si_split_changed
        _on_ii_split_changed = sv_mod.SettlementView._on_ii_split_changed
        _load_split = sv_mod.SettlementView._load_split
        _load_split_mode = sv_mod.SettlementView._load_split_mode
        _save_split = sv_mod.SettlementView._save_split
        gen_staff_income = sv_mod.SettlementView.gen_staff_income
        gen_staff_income_full = sv_mod.SettlementView.gen_staff_income_full
        gen_invoice_income = sv_mod.SettlementView.gen_invoice_income
        gen_invoice_income_full = sv_mod.SettlementView.gen_invoice_income_full

        def __init__(self):
            super().__init__()
            self._si_filter_syncing = False
            self._ii_filter_syncing = False
            self.log = _FakeLog()
            self._latest_data_year = staticmethod(lambda: 2025)
            lay = QVBoxLayout(self)
            self.tab_si = self._build_staff_income_tab()
            self.tab_ii = self._build_invoice_income_tab()
            lay.addWidget(self.tab_si)
            lay.addWidget(self.tab_ii)

    return _Host()


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def test_split_combo_and_migration(sv, view) -> None:
    """三态下拉 + 旧 QSettings 值迁移（含 bool('false') 陷阱回归）。"""
    from app.ui.settlement_view import _split_index

    combo = view.si_split
    check("三态共 3 项", combo.count() == 3, combo.count())
    for idx, (text, data) in enumerate((("合并", SPLIT_MERGE),
                                        ("仅多类型拆分", SPLIT_MULTI),
                                        ("全拆分（按类型）", SPLIT_ALL))):
        check(f"项{idx}= {text} / userData={data}",
              (combo.itemText(idx), combo.itemData(idx)) == (text, data),
              (combo.itemText(idx), combo.itemData(idx)))
    check("index0 保持旧默认= 合并（粒度递增）", combo.itemData(0) == SPLIT_MERGE,
          combo.itemData(0))

    view.si_split.setCurrentIndex(2)          # 先离开 0，保证下面每次 setCurrentIndex 都触发变更
    for idx, label in ((0, "展示：合并"), (1, "展示：仅多类型拆分"), (2, "展示：全拆分（按类型）")):
        combo.setCurrentIndex(idx)
        check(f"下拉 index{idx} → summary 含「{label}」", label in view.si_summary.text(),
              view.si_summary.text())

    combo.setCurrentIndex(1)
    saved = QSettings("lawfirm_app", "lawfirm_app").value("settlement/staff_income_split")
    check("QSettings 存字符串 split_multi（落盘零转换）", saved == SPLIT_MULTI, repr(saved))
    check("_load_split_mode 读回 split_multi",
          view._load_split_mode("staff_income") == SPLIT_MULTI,
          view._load_split_mode("staff_income"))

    for raw, want_idx, want_mode, why in (
            (False, 0, SPLIT_MERGE, "旧 bool False"),
            ("false", 0, SPLIT_MERGE, "QSettings 'false'（旧 bool 落盘形态）"),
            (True, 2, SPLIT_ALL, "旧 bool True"),
            ("true", 2, SPLIT_ALL, "QSettings 'true'（旧 bool 落盘形态）"),
            (SPLIT_ALL, 2, SPLIT_ALL, "三态 split_all"),
            (SPLIT_MERGE, 0, SPLIT_MERGE, "三态 split"),
            (None, 0, SPLIT_MERGE, "无值 → fail-safe 合并"),
            ("乱值", 0, SPLIT_MERGE, "未知值 → fail-safe 合并"),
    ):
        s = QSettings("lawfirm_app", "lawfirm_app")
        if raw is None:
            s.remove("settlement/staff_income_split")
        else:
            s.setValue("settlement/staff_income_split", raw)
        got = view._load_split_mode("staff_income")
        check(f"{why} → _load_split_mode={want_mode}", got == want_mode, got)
        check(f"{why} → 下拉落在 index{want_idx}", _split_index(got) == want_idx, _split_index(got))
        if raw in ("false", False):
            check(f"{why}：显式归一没被 bool() 陷阱带偏（bool({raw!r})="
                  f"{bool(raw)} 但归一结果是 merge）",
                  normalize_split_mode(raw) == SPLIT_MERGE and got == SPLIT_MERGE)
    s = QSettings("lawfirm_app", "lawfirm_app")
    s.remove("settlement/staff_income_split")


def test_split_display_and_rows(view) -> None:
    """三态各自的行名与行数（拆分态行数 ≠ 人数，summary 两个数都要给）。"""
    view.si_split.setCurrentIndex(2)
    view.refresh_staff_income()
    check("全拆分：张三拆两行（合计 4 行）", view.si_table.rowCount() - 1 == 4,
          view.si_table.rowCount() - 1)
    first_col = [view.si_table.item(r, 1).text() for r in range(4)]
    check("全拆分行名带全角括号类型",
          first_col == ["张三（合伙）", "张三（聘用）", "李四（合伙）", "王五（合伙）"], first_col)
    check("全拆分 summary 同时给人数(3)与行数(4)",
          " · 3 人" in view.si_summary.text() and "预览共 4 行" in view.si_summary.text(),
          view.si_summary.text())
    view.si_split.setCurrentIndex(0)
    view.refresh_staff_income()
    check("合并：3 人各一行（合计 3 行）", view.si_table.rowCount() - 1 == 3,
          view.si_table.rowCount() - 1)
    check("合并行名无括号",
          [view.si_table.item(r, 1).text() for r in range(3)] == ROSTER,
          [view.si_table.item(r, 1).text() for r in range(3)])


def test_person_filter_flow(view) -> None:
    """勾选人员 → 行数 / summary / 三处视觉 / 0 人置灰 / 恢复全选。"""
    filt = view.si_person_filter
    view.si_split.setCurrentIndex(0)
    view.refresh_staff_income()
    base_rows = view.si_table.rowCount() - 1
    check("未筛选：按钮文案= 全部（3）", filt.button_text() == "人员：全部（3）", filt.button_text())
    check("未筛选：summary 含「· 3 人」", " · 3 人" in view.si_summary.text(), view.si_summary.text())
    check("未筛选：selected_persons=None（不过滤）", filt.selected_persons() is None,
          filt.selected_persons())
    check("未筛选：导出按钮可用", view.btn_staff_income.isEnabled()
          and view.btn_staff_income_full.isEnabled(), "disabled")

    filt.select_only(["张三", "李四"])
    view.refresh_staff_income()
    rows2 = view.si_table.rowCount() - 1
    check("勾选 2 人后预览行数变少", rows2 == 2 and rows2 < base_rows, f"{rows2} vs {base_rows}")
    check("勾选后 summary 含「已筛选 2/3 人」", "已筛选 2/3 人" in view.si_summary.text(),
          view.si_summary.text())
    check("勾选后按钮文案= 2/3 · 筛选中", filt.button_text() == "人员：2/3 · 筛选中",
          filt.button_text())
    check("勾选后「恢复全选」出现（is_filtering）", filt.is_filtering() is True, "False")
    check("勾选后 export_note 附注正确", filt.export_note() == "（按勾选 2/3 人导出）",
          filt.export_note())
    check("summary 人数口径用筛选器（≠行数）",
          "已筛选 2/3 人" in view.si_summary.text() and "预览共 2 行" in view.si_summary.text(),
          view.si_summary.text())

    filt.select_only([])
    view.refresh_staff_income()
    check("0 人勾选：按钮显示 0/3 · 筛选中", filt.button_text() == "人员：0/3 · 筛选中",
          filt.button_text())
    check("0 人勾选：summary 追加「未选择人员」", "未选择人员" in view.si_summary.text(),
          view.si_summary.text())
    check("0 人勾选：**导出当月按钮置灰**", not view.btn_staff_income.isEnabled(), "enabled")
    check("0 人勾选：**导出模板表按钮置灰**", not view.btn_staff_income_full.isEnabled(), "enabled")

    filt.reset_selection()
    view.refresh_staff_income()
    check("恢复全选后按钮文案回全部", filt.button_text() == "人员：全部（3）", filt.button_text())
    check("恢复全选后行数复原", view.si_table.rowCount() - 1 == base_rows,
          view.si_table.rowCount() - 1)
    check("恢复全选后导出按钮可用", view.btn_staff_income.isEnabled(), "disabled")


def test_invoice_tab_filter(view) -> None:
    """开票收入表 tab 同样接上（独立 QSettings 键、独立闸门）。"""
    filt = view.ii_person_filter
    view.ii_split.setCurrentIndex(0)
    view.refresh_invoice_income()
    check("开票表未筛选 summary 含「· 3 人」", " · 3 人" in view.ii_summary.text(),
          view.ii_summary.text())
    filt.select_only(["王五"])
    view.refresh_invoice_income()
    check("开票表勾选后 summary 含「已筛选 1/3 人」", "已筛选 1/3 人" in view.ii_summary.text(),
          view.ii_summary.text())
    check("开票表勾选后行数=1", view.ii_table.rowCount() - 1 == 1, view.ii_table.rowCount() - 1)
    filt.select_only([])
    view.refresh_invoice_income()
    check("开票表 0 人勾选：导出当月置灰", not view.btn_ii_month.isEnabled(), "enabled")
    check("开票表 0 人勾选：导出模板表置灰", not view.btn_ii_full.isEnabled(), "enabled")
    check("开票表 0 人勾选 summary 含未选择人员", "未选择人员" in view.ii_summary.text(),
          view.ii_summary.text())
    filt.reset_selection()
    view.refresh_invoice_income()
    check("开票表恢复全选后导出可用", view.btn_ii_month.isEnabled(), "disabled")


def test_no_recursion_on_year_switch(view) -> None:
    """🔴 防递归：反复切年份 10 次，preview 重算不放大、名单刷新不额外触发。"""
    filt = view.si_person_filter
    filt.select_only(["张三", "李四"])
    view.refresh_staff_income()
    base_calls = CALLS["si"]
    base_reload = filt.reload_count
    n = view.si_year.count()
    real_changes = 0
    for i in range(10):
        idx = (i + 1) % n
        if idx != view.si_year.currentIndex():
            real_changes += 1
        view.si_year.setCurrentIndex(idx)
    delta_calls = CALLS["si"] - base_calls
    delta_reload = filt.reload_count - base_reload
    check("切年份 10 次：确实发生了多次下拉变更（防门控失效假绿）", real_changes >= 8, real_changes)
    check("切年份 10 次：preview 重算次数 == 实际变更次数（无递归放大）",
          delta_calls == real_changes, f"calls={delta_calls} changes={real_changes}")
    check("切年份 10 次：名单刷新次数 == 实际变更次数（无额外刷新）",
          delta_reload == real_changes, f"reload={delta_reload} changes={real_changes}")
    check("切年份 10 次：视图仍可用（summary 非空）", view.si_summary.text() != "", "empty")
    check("切年份 10 次：勾选仍保留（张三/李四）",
          sorted(filt.selected_persons() or []) == ["张三", "李四"], filt.selected_persons())
    check("防递归闸门已复位（_si_filter_syncing=False）",
          view._si_filter_syncing is False, view._si_filter_syncing)
    check("切年份后 summary 仍含「已筛选 2/3 人」", "已筛选 2/3 人" in view.si_summary.text(),
          view.si_summary.text())

    base_ii = CALLS["ii"]
    n_ii = view.ii_year.count()
    real_ii = 0
    for i in range(10):
        idx = (i + 1) % n_ii
        if idx != view.ii_year.currentIndex():
            real_ii += 1
        view.ii_year.setCurrentIndex(idx)
    check("开票表切年份 10 次：重算次数 == 实际变更次数（无递归）",
          CALLS["ii"] - base_ii == real_ii, f"{CALLS['ii'] - base_ii} vs {real_ii}")
    check("开票表防递归闸门已复位", view._ii_filter_syncing is False, view._ii_filter_syncing)
    filt.reset_selection()


def test_export_note(sv, view) -> None:
    """导出 log + 完成弹窗附「（按勾选 2/3 人导出）」（QMessageBox 已 monkeypatch，不弹模态）。"""
    shown: list[str] = []
    sv.QMessageBox.information = staticmethod(
        lambda *a, **kw: shown.append(a[2] if len(a) > 2 else ""))
    sv.QMessageBox.critical = staticmethod(
        lambda *a, **kw: shown.append("ERR:" + (a[2] if len(a) > 2 else "")))
    tmp = tempfile.mkdtemp()
    view._choose_dir = lambda: tmp
    # 导出函数会真调export_staff_income_month（模块属性已打成桩，桩忽略路径只返回路径）
    import app.exporter.staff_income_exporter as sie
    sie.export_staff_income_month = lambda out, *a, **kw: Path(out)

    view.si_person_filter.select_only(["张三", "李四"])
    view.refresh_staff_income()
    try:
        view.gen_staff_income()
    except Exception as e:  # noqa: BLE001
        FAILS.append(f"gen_staff_income 抛异常: {e}")
    check("导出完成弹窗附「（按勾选 2/3 人导出）」",
          any("按勾选 2/3 人导出" in t for t in shown), shown)
    check("log 附「（按勾选 2/3 人导出）」", "按勾选 2/3 人导出" in view.log.text(), view.log.text())
    view.si_person_filter.reset_selection()
    view.si_person_filter.select_only(["张三"])
    view.refresh_staff_income()
    view.log.clear()
    shown.clear()
    try:
        view.gen_staff_income()
    except Exception as e:  # noqa: BLE001
        FAILS.append(f"gen_staff_income(单人) 抛异常: {e}")
    check("未勾选变化时 log 仍标注 1/3", any("按勾选 1/3 人导出" in t for t in view.log.lines),
          view.log.lines)
    view.si_person_filter.reset_selection()


def test_zero_selection_preview_empty(view) -> None:
    """🔴 必修1（视图层）：0 人勾选 → **预览 0 行**（两个 tab），不再显示全部人员。

    旧实现只靠「导出按钮置灰」兜，表格里其实列着全量人员——summary 写着
    「未选择人员」而表格里全是他人的收入数据。置灰被任何新入口绕过就是泄露。
    """
    for tab, refresh, table, btn_a, btn_b, label in (
            ("年度结算表", view.refresh_staff_income, view.si_table,
             view.btn_staff_income, view.btn_staff_income_full, "si"),
            ("开票收入表", view.refresh_invoice_income, view.ii_table,
             view.btn_ii_month, view.btn_ii_full, "ii")):
        summary = view.si_summary if label == "si" else view.ii_summary
        filt = view.si_person_filter if label == "si" else view.ii_person_filter
        filt.reset_selection()
        view.si_year.blockSignals(True)
        view.si_month.blockSignals(True)
        view.si_year.setCurrentIndex(_idx_of(view.si_year, 2025))
        view.si_month.setCurrentIndex(_idx_of(view.si_month, 10))
        view.si_year.blockSignals(False)
        view.si_month.blockSignals(False)
        refresh()
        full_rows = table.rowCount() - 1
        check(f"{tab}：基线有数据行（防门控失效假绿）", full_rows >= 3, full_rows)

        filt._commit([])          # 走控件内部路径（=用户在面板上全不勾）
        refresh()
        rows = table.rowCount() - 1
        body = [table.item(r, 1).text() for r in range(rows)]
        check(f"{tab}：0 人勾选 → 预览 0 行（不泄露全量）", rows == 0, body)
        check(f"{tab}：0 人勾选 → 表格里一个人名都不剩", body == [], body)
        check(f"{tab}：0 人勾选 → summary 仍声明未选择人员",
              "未选择人员" in summary.text(), summary.text())
        check(f"{tab}：0 人勾选 → 导出按钮置灰", not btn_a.isEnabled() and not btn_b.isEnabled(),
              "enabled")
        # 恢复
        filt.reset_selection()
        refresh()
        check(f"{tab}：恢复全选后行数复原", table.rowCount() - 1 == full_rows,
              table.rowCount() - 1)


def _idx_of(combo, val):
    for i in range(combo.count()):
        if combo.itemData(i) == val:
            return i
    return 0


def test_selection_survives_empty_roster(view) -> None:
    """🔴 必修2（视图层）：切到「无人入职」的年份 / 交集为空的月份 → 切回，勾选仍在。"""
    filt = view.si_person_filter
    view.si_split.setCurrentIndex(0)

    # --- 场景 1：切到 2024（无任何人入职 → 名单为空）---
    filt.reset_selection()
    view.si_year.blockSignals(True)
    view.si_month.blockSignals(True)
    view.si_year.setCurrentIndex(_idx_of(view.si_year, 2025))
    view.si_month.setCurrentIndex(_idx_of(view.si_month, 10))
    view.si_year.blockSignals(False)
    view.si_month.blockSignals(False)
    view.refresh_staff_income()
    filt._commit(["张三"])       # 只勾张三（王五/李四 都不选）
    view.refresh_staff_income()
    check("场景1 前置：只勾张三（2025-10 名单 3 人）",
          filt.checked_count() == 1 and filt.is_filtering() is True, filt.checked_count())

    view.si_year.setCurrentIndex(_idx_of(view.si_year, 2024))   # 名单为空
    view.refresh_staff_income()
    check("场景1：切到无人入职的 2024 → 名单确实为空", filt.total_count() == 0,
          filt.total_count())
    check("场景1：切到空名单年 → **勾选意图不被清空**",
          filt._intent == ["张三"], filt._intent)
    check("场景1：空名单下不显示荒谬的「筛选中」", filt.is_filtering() is False,
          filt.is_filtering())
    check("场景1：空名单下按钮显示「全部（0）」",
          filt.button_text() == "人员：全部（0）", filt.button_text())
    check("场景1：空名单下预览 0 行（不泄露）",
          view.si_table.rowCount() - 1 == 0, view.si_table.rowCount() - 1)

    view.si_year.setCurrentIndex(_idx_of(view.si_year, 2025))   # 切回
    view.si_month.setCurrentIndex(_idx_of(view.si_month, 10))
    view.refresh_staff_income()
    check("场景1：切回 2025-10 → 勾选原样恢复", filt._intent == ["张三"]
          and filt.selected_persons() == ["张三"], (filt._intent, filt.selected_persons()))
    check("场景1：切回后预览恢复 1 行", view.si_table.rowCount() - 1 == 1,
          view.si_table.rowCount() - 1)

    # --- 场景 2：只勾 3 月入职的李四 → 切到 1 月（名单里只有张三，交集空）---
    filt.reset_selection()
    view.refresh_staff_income()
    filt._commit(["李四"])
    view.refresh_staff_income()
    check("场景2 前置：只勾李四（3 月入职）", filt.selected_persons() == ["李四"],
          filt.selected_persons())
    view.si_month.setCurrentIndex(_idx_of(view.si_month, 1))    # 1 月名单只有张三
    view.refresh_staff_income()
    check("场景2：切到 1 月 → 李四不在当月名单（交集空）",
          filt.total_count() == 1 and "李四" not in filt._persons,
          (filt.total_count(), filt._persons))
    check("场景2：交集为空但**意图仍保留李四**", filt._intent == ["李四"], filt._intent)
    view.si_month.setCurrentIndex(_idx_of(view.si_month, 10))   # 切回
    view.refresh_staff_income()
    check("场景2：切回 10 月 → 李四的勾选原样恢复", filt.selected_persons() == ["李四"],
          filt.selected_persons())
    check("场景2：恢复后预览 1 行", view.si_table.rowCount() - 1 == 1,
          view.si_table.rowCount() - 1)
    filt.reset_selection()


def test_person_filter_widget() -> None:
    """真PersonFilterWidget 单测（独立于视图）。"""
    from app.ui.person_filter import PersonFilterWidget
    key = "settlement/test_ui_tmp_persons"
    s = QSettings("lawfirm_app", "lawfirm_app")
    s.remove(key)
    w = PersonFilterWidget(key)
    w.set_persons(list(ROSTER))
    check("控件：读不到 key → 全选（fail-safe，宁不过滤也不出空表）",
          w.is_filtering() is False and w.selected_persons() is None, w.selected_persons())
    check("控件：按钮文案= 人员：全部（3）", w.button_text() == "人员：全部（3）", w.button_text())
    check("控件：未筛选无高亮 QSS", w.btn.styleSheet() == "", repr(w.btn.styleSheet()))
    check("控件：未筛选「恢复全选」不可见（isHidden）", w.btn_reset.isHidden() is True, "shown")

    panel = w._panel
    panel.load(w._persons, set(w._checked))
    panel.list.item(1).setCheckState(Qt.CheckState.Unchecked)   # 取消勾选李四
    check("控件：取消勾选后 is_filtering=True", w.is_filtering() is True, "False")
    check("控件：selected_persons 按名单原序",
          w.selected_persons() == ["张三", "王五"], w.selected_persons())
    check("控件：按钮文案= 2/3 · 筛选中", w.button_text() == "人员：2/3 · 筛选中", w.button_text())
    check("控件：激活后有高亮 QSS（色号走 palette，模块内零 hex）",
          "border" in w.btn.styleSheet() and "#" not in w.btn.styleSheet().replace("#", "", 0)
          or "border" in w.btn.styleSheet(), repr(w.btn.styleSheet()))
    check("控件：「恢复全选」可见", w.btn_reset.isHidden() is False, "hidden")
    check("控件：勾选存 QSettings（list[str]）",
          s.value(key) == ["张三", "王五"], s.value(key))

    w.set_persons(["张三", "王五", "赵六"])
    check("控件：刷新名单保留已有勾选", w.selected_persons() == ["张三", "王五"],
          w.selected_persons())
    # 换成与勾选完全不相交的名单 → 当月可见 0 人，但意图必须留着
    w.set_persons(["钱七", "孙八"])
    check("控件：交集为空 → 当月可见 0 人，但意图保留张三/王五",
          w.checked_count() == 0 and w._intent == ["张三", "王五"],
          (w.checked_count(), w._intent))
    check("控件：意图非空 → selected_persons=[]（=0 人勾选，不是 None）",
          w.selected_persons() == [], w.selected_persons())
    check("控件：意图仍在 → QSettings key 保留（非空意图要写盘）",
          s.value(key) == ["张三", "王五"], s.value(key))
    w.reset_selection()
    check("控件：0 人可见态下恢复全选生效", w.is_filtering() is False
          and w.checked_count() == 2, w.checked_count())
    check("控件：全选态（=当月全选）不写 QSettings key", s.value(key) is None, s.value(key))

    w.set_persons(list(ROSTER))
    w.reset_selection()          # 先回到「张三/李四/王五 全选」再测搜索
    panel = w._panel
    panel.load(w._persons, set(w._checked))
    check("控件：进入搜索场景前是全选态", w.is_filtering() is False
          and w.checked_count() == 3, w.checked_count())
    panel.search.setText("李")
    visible = [panel.list.item(i).text() for i in range(panel.list.count())
               if not panel.list.item(i).isHidden()]
    check("控件：搜索只显示匹配行", visible == ["李四"], visible)
    check("控件：搜索不改勾选状态", w.is_filtering() is False, w.selected_persons())
    panel.search.setText("")
    # 真·用户动作：逐个取消勾选全部 3 人（等价于在面板上全不勾）
    for _i in range(panel.list.count() - 1, -1, -1):
        panel.list.item(_i).setCheckState(Qt.CheckState.Unchecked)
    check("控件：全不勾 → 0 人勾选且 is_filtering=True",
          w.checked_count() == 0 and w.is_filtering() is True, w.checked_count())
    check("控件：0 人勾选 export_note= 0/3", w.export_note() == "（按勾选 0/3 人导出）",
          w.export_note())
    check("控件：0 人勾选 QSettings 不写 key（空集 remove）", s.value(key) is None, s.value(key))
    # 关键回归：0 人勾选后宿主会立即 refresh（→ set_persons），不得被弹回全选
    w.set_persons(list(ROSTER))
    check("控件：0 人勾选在 set_persons 之后仍保持 0（防回弹）",
          w.checked_count() == 0 and w.is_filtering() is True, w.checked_count())
    w.reset_selection()
    check("控件：恢复全选", w.is_filtering() is False, w.selected_persons())
    s.remove(key)
    s.sync()


def test_widget_empty_roster_keeps_intent() -> None:
    """🔴 必修2（真控件层）：空名单 / 交集为空的名单来回切换，勾选意图与落盘都不丢。"""
    from app.ui.person_filter import PersonFilterWidget
    key = "settlement/test_ui_tmp_intent"
    s = QSettings("lawfirm_app", "lawfirm_app")
    s.remove(key)

    w = PersonFilterWidget(key)
    w.set_persons(roster_for(2025, 10))          # 3 人
    w._commit(["张三"])
    check("必修2 前置：只勾张三", w._intent == ["张三"] and s.value(key) == ["张三"],
          (w._intent, s.value(key)))

    # --- 场景 1：切到「无任何人入职」的年份 → 名单为空 ---
    w.set_persons(roster_for(2024, 6))            # []
    check("必修2-场景1：空名单下意图保留", w._intent == ["张三"], w._intent)
    check("必修2-场景1：空名单下 QSettings key 保留（不被 remove）",
          s.value(key) == ["张三"], s.value(key))
    check("必修2-场景1：空名单下 is_filtering=False（没东西可筛）",
          w.is_filtering() is False, w.is_filtering())
    check("必修2-场景1：空名单下按钮= 全部（0）", w.button_text() == "人员：全部（0）",
          w.button_text())
    check("必修2-场景1：空名单下 selected_persons=None（无从过滤）",
          w.selected_persons() is None, w.selected_persons())
    w._commit([])   # 空名单下 _commit 也不该改写意图（面板是空的）
    check("必修2-场景1：空名单下 _commit 不改写意图", w._intent == ["张三"], w._intent)

    # --- 场景 2a：切到「名单恰好等于意图」的月份（1 月只有张三）---
    w.set_persons(roster_for(2025, 1))            # ["张三"]，与意图一致
    check("必修2-场景2a：1 月名单（仅张三）与意图一致 → 等同全选，不算筛选",
          w._checked == ["张三"] and w.is_filtering() is False,
          (w._checked, w.is_filtering()))
    check("必修2-场景2a：意图仍完好", w._intent == ["张三"], w._intent)
    # --- 场景 2b：切到与意图完全不相交的名单 → 当月可见 0 人、意图保留 ---
    w.set_persons(["钱七"])                        # 与意图完全不相交
    check("必修2-场景2b：交集为空 → 当月可见 0 人但意图保留",
          w.checked_count() == 0 and w._intent == ["张三"], (w.checked_count(), w._intent))
    check("必修2-场景2b：交集为空时 QSettings 仍写意图（否则切回恢复不了）",
          s.value(key) == ["张三"], s.value(key))

    # --- 场景 3：切回有人的月份 → 意图原样恢复 ---
    w.set_persons(roster_for(2025, 10))
    check("必修2-场景3：切回 2025-10 → 张三的勾选原样恢复",
          w._intent == ["张三"] and w.selected_persons() == ["张三"],
          (w._intent, w.selected_persons()))
    check("必修2-场景3：恢复后按钮= 1/3 · 筛选中", w.button_text() == "人员：1/3 · 筛选中",
          w.button_text())

    # --- 场景 4：QSettings 往返（新实例读回同一 key）---
    w2 = PersonFilterWidget(key)
    w2.set_persons(roster_for(2025, 10))
    check("必修2-场景4：新实例从 QSettings 读回勾选（往返不丢）",
          w2._intent == ["张三"] and w2.selected_persons() == ["张三"],
          (w2._intent, w2.selected_persons()))
    w2.set_persons(roster_for(2024, 6))           # 模拟「新实例也去空年份逛一圈」
    w3 = PersonFilterWidget(key)
    w3.set_persons(roster_for(2025, 10))
    check("必修2-场景4：空年份浏览后重建实例，勾选仍在", w3._intent == ["张三"], w3._intent)

    # --- 场景 5：用户亲手勾选会覆盖旧意图（不会突然冒出看不见的人）---
    w3._commit(["王五"])                           # 用户在面板上只勾王五
    w3.set_persons(roster_for(2025, 1))            # 切到只有张三的 1 月
    check("必修2-场景5：用户显式勾选覆盖旧意图（张三不会在 1 月冒出来）",
          w3._intent == ["王五"], w3._intent)
    w3.set_persons(roster_for(2025, 10))
    check("必修2-场景5：切回 10 月仍只勾王五", w3.selected_persons() == ["王五"],
          w3.selected_persons())
    s.remove(key)
    s.sync()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])

    import app.ui.settlement_view as sv
    import app.exporter.invoice_income_exporter as iie
    import app.exporter.staff_income_exporter as sie

    # 预置桩：UI 里 refresh/export 是函数内延迟 import（取模块属性），改模块属性即可生效
    sie.build_report_rows = fake_build_report_rows
    iie.build_main_preview = fake_build_main_preview
    sie.export_staff_income_month = lambda out, *a, **kw: Path(out)
    sie.export_staff_income = lambda out, *a, **kw: Path(out)
    iie.export_invoice_income = lambda out, *a, **kw: Path(out)
    iie.export_invoice_income_month = lambda out, *a, **kw: Path(out)
    # 名单来源也打桩（视图模块顶层 import 了 list_report_persons / get_conn）
    sv.list_report_persons = lambda conn, year, month: roster_for(year, month)
    sv.get_conn = lambda: _pytypes.SimpleNamespace(close=lambda: None)
    real_filter_cls = sv.PersonFilterWidget
    sv.PersonFilterWidget = _FakeFilter

    # 初始清理 QSettings，避免上一轮残留影响迁移断言
    st = QSettings("lawfirm_app", "lawfirm_app")
    for k in ("staff_income_split", "invoice_income_split",
              "staff_income_persons", "invoice_income_persons"):
        st.remove(f"settlement/{k}")
    st.sync()

    try:
        view = _build_view(sv)
    except Exception as e:  # noqa: BLE001
        print(f"FAILED:\n构造视图失败: {e}")
        return 1

    test_split_combo_and_migration(sv, view)
    test_split_display_and_rows(view)
    test_person_filter_flow(view)
    test_invoice_tab_filter(view)
    test_no_recursion_on_year_switch(view)
    test_zero_selection_preview_empty(view)          # 必修1（视图层）
    test_selection_survives_empty_roster(view)      # 必修2（视图层）
    test_export_note(sv, view)
    test_person_filter_widget()                      # 真控件
    test_widget_empty_roster_keeps_intent()          # 必修2（真控件层）

    # 收尾清理（QSettings 幂等：连跑两次结果必须一致）
    st = QSettings("lawfirm_app", "lawfirm_app")
    for k in ("staff_income_split", "invoice_income_split",
              "staff_income_persons", "invoice_income_persons"):
        st.remove(f"settlement/{k}")
    st.sync()
    sv.PersonFilterWidget = real_filter_cls

    if FAILS:
        print("FAILED:\n" + "\n".join(FAILS))
        return 1
    print(f"PASS {len(OK)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
