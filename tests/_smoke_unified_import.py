"""offscreen 冒烟：统一导入确认对话框（问题修正 + 预览确认 合并）。

覆盖：
- 一张表同时承载解析行与问题行，状态筛选胶囊切换
- 问题行就地修正 → 保存后立即重算并刷新，真实 data 不被修改
- 各经办人已收覆盖值回写
- 预收款问题行修正
- 不允许跳过（阶段 1a，2026-09-18 用户拍板）：全对话框已删除「跳过」按钮，问题行只有
  「保存修改」一个出口；「确认入库」升级为**硬拦** —— 只要还存在任一「待补录 / 待确认」
  行即拒绝入库（不再问「是否继续」），真实 data 一行不写
- 「确认入库」就地校验：校验不过留在对话框内、真实 data 不变（须先清零两类未处理行）
- 校验通过才合并：problems 清空、sheet 合计含修正行
- 阶段 2-2（第 10 节）：A1 应收账款(sheet3)行进表 / A2 原因三态 / A3 就地编辑回写
  （需补录行不出收款；已在库行「确认」后置 receipt_confirmed 交 A10 追加收款；
   A11 多期收款展开为同名多行，合计仍等于开票总额）
- 阶段 3（第 11 节）：B2g 行内「补录原票 / 查看原票 / 修改补录」按钮（需补录行、普通发票行、
  已在库行、红字行取原票号四态）／B2c **不立即写库**（确定只收集进 `_backfills`、
  取消一行不写）／B2h 补录后按钮翻转 + 原因「已补录，请确认收款」+ 底部计数同步／
  B2d/B2e 写前校验与「本次已填过同票号」拦截。
- 阶段 4-2（散落各节，标签含「4-2」「A 甲」）：状态四态 + 默认筛选「待补录」+ 待补录排在
  待确认之前；补录完**不再直接高置信**而是落到「待确认」并**回显**补录里填的收款；
  待补录行不显示「确认」（点了给可读提示、不写数据）。
- 批 2（第 12 节）：导入前「台账 ⇄ 库」三维比对（金额 / 经办人分摊 / 已收认定，
  sheet3 除外）判进四态 —— 命中 → 待确认（原因带两侧对照值）；一致 → 零扰动；
  「确认」是唯一消除机制（不吃 has_split 豁免）；post 模式刻意不比对。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

import app.ui.unified_import_dialog as U  # noqa: E402
from app.ui.unified_import_dialog import UnifiedImportDialog  # noqa: E402

# 阶段 1（C3）：`_apply_resolved` → `split_deferred` 需要「库中已有票号集合」。
# 本冒烟不碰真实 DB（data/lawfirm.db）→ 把取号打桩成空库，判定只由测试数据决定。
# `_LIB` 可变：第 10 节用它把某些票号「变成已在库」以覆盖「已入库，请确认收款」态。
import app.engine.backfill as _bf  # noqa: E402
import app.importer.importer as _imp0  # noqa: E402

_LIB: set = set()
_bf.library_invoice_nos = lambda conn=None: set(_LIB)
_imp0.library_invoice_nos = lambda conn=None: set(_LIB)

# 批 2：pre 模式 load_data 会按账期读**真实 DB**建「台账⇄库」比对上下文。
# **方案 B（2026-09-18）之后**：比对只跑「票已在库」的行 → 本冒烟的假票号
# （INV-1 等）本来就不会被比对（旧写法把它们一律报成「库中缺失，请确认」，
# 既有断言全灭，这正是方案 B 要消灭的假待办）。此桩保留只为「不碰真实库 +
# 断言确定性」，不再承担正确性职责；批 2 自身的比对行为全部集中在第 12 节
# 用**临时库 + 真函数**验证（12-10 会还原真 build_lib_context）。
import app.engine.review_compare as _rc  # noqa: E402

_rc_build_lib_context_real = _rc.build_lib_context
_rc.build_lib_context = lambda period, invoice_nos=None, batch_id=None, conn=None: None


class _MB:
    """offscreen 下 QMessageBox.exec() 会阻塞，用桩替换：只记录、不弹窗。"""
    calls: list = []

    class StandardButton:
        Yes, No = 1, 0

    @staticmethod
    def warning(parent, title, text, *a, **k):
        _MB.calls.append(("warning", text))

    @staticmethod
    def question(parent, title, text, *a, **k):
        _MB.calls.append(("question", text))
        return 1  # Yes

    @staticmethod
    def information(parent, title, text, *a, **k):
        _MB.calls.append(("information", text))


U.QMessageBox = _MB
# 第 10 节会用同名多行（= 多期收款）触发 ProblemFixPanel._confirm_merge，
# 它用的是 problem_fix_panel 自己 import 的 QMessageBox → 一并换成桩，否则 offscreen 下阻塞。
import app.ui.problem_fix_panel as _pfp  # noqa: E402

_pfp.QMessageBox = _MB

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")


STAFF = ["周立生", "陈娟", "胡坚", "柳立中"]
HEADER = ["发票号码", "购方名称", "价税合计", "经办人", "备注"]


def mk_inv(no, total, sheet="sheet1", remark=None, handler="周立生"):
    return {
        "sheet": sheet,
        "sheet_name": {"sheet1": "已开票已入账", "sheet3": "应收账款"}.get(sheet, sheet),
        "row_no": 10,
        "header": HEADER,
        "raw_row": [no, "某某公司", str(total), handler, ""],
        "invoice_no": no, "invoice_date": "2025-01-15", "buyer": "某某公司",
        "total_amount": total, "handlers": [(handler, float(total))],
        "handler_text": handler, "remark_raw": "",
        "remark": remark or {"receipts": [], "remaining": None, "pure_date": None},
        "case_no": "", "is_red": False, "split_receipts": [],
    }


def mk_problem_inv():
    return {
        "kind": "invoice", "sheet": "sheet1", "row_no": 12,
        "invoice_no": "BAD-1", "buyer": "问题购方", "total_amount": "5000",
        "handler_text": "周立生3000 陈娟2000", "remark_raw": "1.20收",
        "date_text": "2025-01-20", "reason": "经办人列无法解析",
        "header": HEADER, "raw_row": ["BAD-1", "问题购方", "5000", "周立生3000 陈娟2000", "1.20收"],
    }


def mk_problem_pp():
    return {
        "kind": "prepayment", "sheet": "sheet4", "row_no": 5,
        "buyer": "预付购方", "amount_text": "2000",
        "person_text": "胡坚", "date_text": "2025-01-08", "reason": "金额无法解析",
        "header": HEADER, "raw_row": ["", "预付购方", "2000", "胡坚", "2025-01-08"],
    }


def mk_data():
    return {
        "period": "2025-01",
        "invoices": [
            # 高置信：sheet1 + 收款备注齐全
            mk_inv("INV-1", 1000.0,
                   remark={"receipts": [("2025-01", 0)], "remaining": None, "pure_date": None}),
            # 待确认：应收账款纯日期
            mk_inv("INV-2", 2000.0, sheet="sheet3",
                   remark={"receipts": [], "remaining": None, "pure_date": "2025-01-20"}),
        ],
        "prepayments": [],
        "problems": [mk_problem_inv(), mk_problem_pp()],
        "sheet_totals": {"sheet1": 1000.0},
        "sheet12_total": 1000.0,
    }


def statuses(dlg):
    return [r["status"] for r in dlg._rows]


def _show_all(dlg):
    """切到「全部」筛选。

    阶段 4-2 起默认筛选 = 待补录；凡是要按 `_rows` 下标选行、或断言「已处理完的行」
    的用例，都必须先切「全部」，否则默认视图里根本没有该行。

    阶段 5 起筛选栏插入了「已补录」（待确认与已确认之间）→ 「全部」的下标从 5 变 6，
    故一律用 `U.FILTER_ALL`，不要再写死数字。
    """
    dlg._grp.button(U.FILTER_ALL).setChecked(True)
    dlg._render()


def _select(dlg, r):
    """选中某一行并刷新右侧。

    必须用 `setCurrentCell`（同时设「当前项」与选择）：`selectRow` 只改选择、不保证改
    当前项，而 `_current_row()` 读的是当前项 → 只 selectRow 会让右侧读到旧行。
    """
    i = dlg._rows.index(r)
    dlg.table.setCurrentCell(i, 0)
    dlg.table.selectRow(i)
    dlg._load_right()


def fill_invoice_fix(dlg):
    """把右侧修正面板填成合法数据（总额 5000 = 周立生 3000 + 陈娟 2000）。"""
    p = dlg.fix_panel
    p.inv_date.setText("2025-01-20")
    p.inv_amount.setText("5000")
    if p.htable.rowCount() < 2:
        p._add_row()
    p.htable.cellWidget(0, 0).setCurrentText("周立生")
    p.htable.item(0, 1).setText("3000")
    p.htable.cellWidget(1, 0).setCurrentText("陈娟")
    p.htable.item(1, 1).setText("2000")


def mk_clean_data():
    """**干净数据**：无「待补录 / 待确认」行 —— 阶段 1a 起 accept 硬拦的放行前提。

    「确认入库」现在只在两类未处理行全清零时才写库，故凡是要走 `accept()` 端到端
    路径的用例都必须用这份数据；`mk_data()` 自带 2 个问题行 + 1 个待补录行，只能用来
    验「硬拦」与「合并口径」。
    """
    return {
        "period": "2025-01",
        "invoices": [mk_inv("INV-HI", 1000.0,
                            remark={"receipts": [("2025-01", 0)],
                                    "remaining": None, "pure_date": None})],
        "prepayments": [], "problems": [],
        "sheet_totals": {"sheet1": 1000.0}, "sheet12_total": 1000.0,
    }


# ---------------------------------------------------------------- 1) 行模型
data = mk_data()
dlg = UnifiedImportDialog(data, "2025-01", STAFF, path="2025.1台账.xlsx")
dlg.show()  # offscreen 下子控件 isVisible() 依赖父窗口已 show
check("构造成功", dlg is not None)
# 阶段 2（A1）：sheet3 行（INV-2）已移出 invoices → 不再是 invoice 行，
# 而是以 kind="deferred" 的「应收账款行」出现；总数仍是 4（1 发票 + 2 问题 + 1 应收）。
check("行模型 = 1 解析行 + 2 问题行 + 1 应收账款行",
      len(dlg._rows) == 4, f"got={len(dlg._rows)}")
_kinds0 = [r["kind"] for r in dlg._rows]
check("A1：应收账款(sheet3)行进表（kind=deferred）", _kinds0.count("deferred") == 1,
      f"got={_kinds0}")
check("A1：INV-2 不再作为 invoice 行出现",
      all(r["ev"]["invoice_no"] != "INV-2" for r in dlg._rows if r["kind"] == "invoice"),
      str([r["ev"]["invoice_no"] for r in dlg._rows if r["kind"] == "invoice"]))
_drow0 = next(r for r in dlg._rows if r["kind"] == "deferred")
check("A1：应收账款行带 d_index 与 need_backfill 语义",
      _drow0["d_index"] == 0 and _drow0["deferred"].get("need_backfill") is True,
      f"d_index={_drow0['d_index']} nb={_drow0['deferred'].get('need_backfill')}")
st = statuses(dlg)
check("阶段4-2：状态含 1 高置信 / 2 待确认 / 1 待补录（应收账款行按待补录计）",
      st.count("待补录") == 1 and st.count("待确认") == 2 and st.count("高置信") == 1,
      f"got={st}")
check("阶段4-2：待补录排最前（最紧急、最前）", st.index("待补录") == 0, f"got={st}")
check("阶段4-2：筛选栏首位 = 待补录、第二位 = 待确认（C 甲）",
      U.FILTERS[0] == "待补录" and U.FILTERS[1] == "待确认", str(U.FILTERS))
check("阶段5：新增「已补录」夹在待确认与已确认之间（A 甲）",
      U.FILTERS[2] == "已补录" and U.FILTERS[3] == "已确认" and U.FILTERS[4] == "高置信",
      str(U.FILTERS))
check("阶段5：FILTER_ALL 指向「全部」（不再写死下标）",
      U.FILTERS[U.FILTER_ALL] == "全部", f"idx={U.FILTER_ALL}")

# ---- A2：右栏「原因 / 疑问」三态 ----
check("A2：票号不在库 → 需补录原票",
      dlg._row_field(_drow0, "reason") == "需补录原票",
      dlg._row_field(_drow0, "reason"))
check("A2：应收账款行「类型」列 = 应收账款",
      dlg._row_field(_drow0, "kind") == "应收账款", dlg._row_field(_drow0, "kind"))
check("A2：应收账款行「收款认定」列按备注推导（纯日期 → 全额）",
      "2025-01" in dlg._row_field(_drow0, "receipt")
      and "全额" in dlg._row_field(_drow0, "receipt"),
      dlg._row_field(_drow0, "receipt"))
_hi0 = next(r for r in dlg._rows if r["kind"] == "invoice" and r["status"] == "高置信")
check("A2：系统无疑问 → 系统判定无疑问",
      dlg._row_field(_hi0, "reason") == "✓ 系统判定无疑问",
      dlg._row_field(_hi0, "reason"))

# 默认筛选「待补录」（阶段 4-2 B 甲）：只剩应收账款那一行
check("阶段4-2：默认筛选=待补录，1 行", dlg.table.rowCount() == 1,
      f"got={dlg.table.rowCount()}")
check("阶段4-2：默认勾选的是首位（待补录）", dlg._grp.checkedId() == 0,
      f"got={dlg._grp.checkedId()}")
dlg._grp.button(U.FILTER_ALL).setChecked(True)  # 全部
dlg._render()
check("切「全部」= 4 行", dlg.table.rowCount() == 4, f"got={dlg.table.rowCount()}")
dlg._grp.button(U.FILTERS.index("待确认")).setChecked(True)
dlg._render()
check("切「待确认」= 2 行", dlg.table.rowCount() == 2, f"got={dlg.table.rowCount()}")
dlg._grp.button(U.FILTERS.index("已补录")).setChecked(True)
dlg._render()
check("阶段5：切「已补录」= 0 行（本次未填过补录）",
      dlg.table.rowCount() == 0, f"got={dlg.table.rowCount()}")
dlg._grp.button(U.FILTERS.index("已确认")).setChecked(True)  # 阶段5：已确认由 2 移到 3
dlg._render()
check("切「已确认」= 0 行（尚未修正/确认）", dlg.table.rowCount() == 0, f"got={dlg.table.rowCount()}")
dlg._grp.button(U.FILTERS.index("高置信")).setChecked(True)  # 阶段5：高置信由 3 移到 4
dlg._render()
check("切「高置信」= 1 行", dlg.table.rowCount() == 1, f"got={dlg.table.rowCount()}")

# 问题行也带原始台账行（底座改动）
dlg._grp.button(1).setChecked(True)  # 待确认（含解析失败问题行）
dlg._render()
dlg.table.selectRow(0)
row0 = dlg._current_row()
check("问题行携带 header/raw_row（可查看原始台账行）",
      bool(row0["problem"].get("header")) and bool(row0["problem"].get("raw_row")))
check("问题行右侧显示修正面板（发票表单）",
      dlg.fix_panel.isVisible() and dlg.fix_panel.inv_box.isVisible())
check("保存按钮对问题行可见；「跳过」按钮已删除（阶段 1a 不允许跳过）",
      dlg.btn_save.isVisible() and not hasattr(dlg, "btn_skiprow"))

# ---------------------------------------------------------------- 2) 就地修正
n_inv_before = len(data["invoices"])
fill_invoice_fix(dlg)
dlg._save_fix()
check("保存后工作副本：sheet3 行移出 invoices、修正行入库（净 ±0）",
      {i["invoice_no"] for i in dlg._work["invoices"]} == {"INV-1", "BAD-1"},
      f"got={[i['invoice_no'] for i in dlg._work['invoices']]}")
check("保存后工作副本：sheet3 行落入 deferred 且打 need_backfill 标记",
      {d["invoice_no"]: d.get("need_backfill") for d in dlg._work.get("deferred", [])}
      == {"INV-2": True},
      f"got={dlg._work.get('deferred')}")
check("保存后真实 data 未被修改", len(data["invoices"]) == n_inv_before,
      f"got={len(data['invoices'])}")
check("修正行(原问题行)不再是待确认",
      any(r["kind"] == "problem" and r["p_index"] == 0 and r["status"] == "高置信"
          for r in dlg._rows),
      f"got={statuses(dlg)}")
check("修正行(原问题行)归入已确认",
      any(r["kind"] == "problem" and r["p_index"] == 0 and r.get("is_confirmed")
          for r in dlg._rows),
      f"got={statuses(dlg)}")
check("sheet 合计含修正行（5000 计入 sheet1）",
      abs(dlg._work["sheet_totals"].get("sheet1", 0.0) - 6000.0) < 0.01,
      f"got={dlg._work['sheet_totals']}")
check("sheet12_total 同步重算", abs(dlg._work["sheet12_total"] - 6000.0) < 0.01,
      f"got={dlg._work['sheet12_total']}")

# ---------------------------------------------------------------- 3) 右侧就地编辑已存在发票 → 各经办人已收 / 收款认定 同步
dlg._grp.button(U.FILTER_ALL).setChecked(True)  # 全部
dlg._render()
target = next(r for r in dlg._rows if r["kind"] == "invoice" and r["inv_idx"] == 0)
dlg.table.selectRow(dlg._rows.index(target))   # 触发 _load_right → set_invoice
# 通过右侧面板把 周立生 收款金额由 1000.00 改为 800.00（不动开票金额）
p = dlg.fix_panel
p.htable.item(0, 2).setText("800.00")
ed = p.read_fix()
dlg._inv_edits[0] = ed
n_work_before = len(dlg._work["invoices"])
dlg._rebuild()
check("右侧编辑不追加新发票（原地更新）",
      len(dlg._work["invoices"]) == n_work_before, f"got={len(dlg._work['invoices'])}")
target = next(r for r in dlg._rows if r["kind"] == "invoice" and r["inv_idx"] == 0)
check("右侧编辑后 各经办人已收 显示 800.00", "800.00" in dlg._recv_text(target), dlg._recv_text(target))
check("右侧编辑后 收款认定 同步更新", "800.00" in target["ev"]["receipt_text"], target["ev"]["receipt_text"])

# ------------------------------- 4) 硬拦：待补录 / 待确认 未清零 → 拒绝入库
calls = {"n": 0}


def bad_validator(d):
    calls["n"] += 1
    return "台账 sheet1+sheet2 合计(6000) ≠ 销项文档本月价税合计(0)"


dlg._validate = bad_validator
_MB.calls.clear()
dlg.accept()
check("硬拦：先被未处理行拦下（校验器未被调用）", calls["n"] == 0, f"got={calls['n']}")
check("硬拦：真实 data 未合并（problems 仍在）", len(data.get("problems", [])) == 2)
check("硬拦：发票数未增加", len(data["invoices"]) == n_inv_before)
_warns4 = [c[1] for c in _MB.calls if c[0] == "warning"]
_n_bf4 = statuses(dlg).count("待补录")
_n_pd4 = statuses(dlg).count("待确认")
check("硬拦：弹「尚有未处理的行」（桩只记 text → 认正文「无法入库」）并分别列出计数",
      any("无法入库" in t for t in _warns4)
      and any(f"「待补录」{_n_bf4} 行" in t and f"「待确认」{_n_pd4} 行" in t
              for t in _warns4),
      str(_warns4[:1]))

# ------------------------------- 4b) 清零后：校验不过 → 留在对话框
d_c = mk_clean_data()
dlg_c = UnifiedImportDialog(d_c, "2025-01", STAFF)
dlg_c.show()
check("4b：干净数据无待补录 / 待确认（accept 可放行）",
      statuses(dlg_c) == ["高置信"], str(statuses(dlg_c)))
_c_conf = []
dlg_c.confirmed.connect(lambda: _c_conf.append(1))
calls_c = {"n": 0}


def bad_validator_c(d):
    calls_c["n"] += 1
    return "台账 sheet1+sheet2 合计(1000) ≠ 销项文档本月价税合计(0)"


dlg_c._validate = bad_validator_c
_MB.calls.clear()
dlg_c.accept()
check("4b：校验不过 → 调用了校验器", calls_c["n"] == 1, f"got={calls_c['n']}")
check("4b：校验不过 → 弹出校验错误正文（QMessageBox.warning 桩只记 text）",
      any("销项文档本月价税合计" in c[1] for c in _MB.calls if c[0] == "warning"),
      str(_MB.calls[-1:]))
check("4b：校验不过 → 真实 data 未被替换（发票仍在 / 无 deferred / 无 confirmed）",
      len(d_c["invoices"]) == 1 and not d_c.get("deferred") and _c_conf == [],
      f"deferred={d_c.get('deferred')} conf={_c_conf}")

# ------------------------------- 5) 清零后：校验通过 → 合并（端到端）
d5 = mk_clean_data()
dlg5 = UnifiedImportDialog(d5, "2025-01", STAFF)
dlg5.show()
_n5 = []
dlg5.confirmed.connect(lambda: _n5.append(1))
_show_all(dlg5)      # 干净数据无「待补录」行 → 默认筛选下表格为空，须先切「全部」
r5 = next(r for r in dlg5._rows if r["kind"] == "invoice" and r["inv_idx"] == 0)
_select(dlg5, r5)
check("5：右栏已载入该发票（各经办人一行）",
      dlg5.fix_panel.htable.rowCount() == 1, str(dlg5.fix_panel.htable.rowCount()))
dlg5.fix_panel.htable.item(0, 2).setText("800.00")   # 各经办人已收 1000 → 800
dlg5._inv_edits[0] = dlg5.fix_panel.read_fix()
dlg5._rebuild()
dlg5._validate = lambda d: None
dlg5.accept()
check("5：校验通过 → 发 confirmed 一次", _n5 == [1], str(_n5))
check("5：校验通过 → 原地替换调用方的**同一个** dict（新增 backfills 键）",
      "backfills" in d5 and d5.get("problems") == [], f"got={sorted(d5)}")
check("5：校验通过 → 右侧编辑的收款写入 split_receipts",
      d5["invoices"][0].get("split_receipts") == [("周立生", 800.0, "2025-01")],
      f"got={d5['invoices'][0].get('split_receipts')}")

# ---------------- 5b) 未清零时（accept 被硬拦）直接验合并口径 _merged_data()
_merged = dlg._merged_data()
check("5b：合并口径 problems 清空", _merged.get("problems") == [],
      f"got={_merged.get('problems')}")
check("5b：合并口径 invoices = sheet1 原票 + 修正行（sheet3 行已移出）",
      {i["invoice_no"] for i in _merged["invoices"]} == {"INV-1", "BAD-1"},
      f"got={[i['invoice_no'] for i in _merged['invoices']]}")
check("5b：合并口径 sheet3 行落进 deferred（未丢失）",
      {d["invoice_no"] for d in _merged.get("deferred", [])} == {"INV-2"},
      f"got={[d['invoice_no'] for d in _merged.get('deferred', [])]}")
check("5b：合并口径含右侧编辑的收款",
      _merged["invoices"][0].get("split_receipts") == [("周立生", 800.0, "2025-01")],
      f"got={_merged['invoices'][0].get('split_receipts')}")
_fixed = _merged["invoices"][-1]
check("5b：修正行透传原始台账行", bool(_fixed.get("header")) and bool(_fixed.get("raw_row")))
check("5b：修正行保留源文件 sheet 分类", _fixed.get("sheet") == "sheet1",
      f"got={_fixed.get('sheet')}")
check("5b：修正行经办人分摊 2 人", len(_fixed["handlers"]) == 2, f"got={_fixed['handlers']}")

# ---------------------------------------- 6) 不允许跳过 + 预收款 + 硬拦不写库
data2 = mk_data()
dlg2 = UnifiedImportDialog(data2, "2025-01", STAFF)
dlg2.show()
dlg2._validate = lambda d: None
# 4-2 后默认筛选 = 待补录，第 0 行已不是问题行 → 先切「全部」按类型定位
_show_all(dlg2)
check("6：已无「跳过」按钮（阶段 1a 不允许跳过）", not hasattr(dlg2, "btn_skiprow"))
inv_prob = next(r for r in dlg2._rows
                if r["kind"] == "problem" and r["problem"]["kind"] == "invoice")
_select(dlg2, inv_prob)
r = dlg2._current_row()
check("定位到发票问题行", r["kind"] == "problem" and r["problem"]["kind"] == "invoice",
      str(r.get("problem")))
check("6：发票问题行仍在「待确认」（唯一出口是「保存修改」）",
      r["status"] == "待确认", r["status"])
# 修正预收款问题行（另一条「待确认」出口）
pp_prob = next(r for r in dlg2._rows
               if r["kind"] == "problem" and r["problem"]["kind"] == "prepayment")
_select(dlg2, pp_prob)
check("预收款行显示预收款表单", dlg2.fix_panel.pp_box.isVisible())
dlg2.fix_panel.pp_date.setText("2025-01-08")
dlg2.fix_panel.pp_amount.setText("2000")
dlg2.fix_panel.pp_person.setText("胡坚")
dlg2._save_fix()
check("预收款修正后 prepayments +1", len(dlg2._work.get("prepayments", [])) == 1,
      f"got={len(dlg2._work.get('prepayments', []))}")
check("6：预收款问题行已离开待确认（只剩发票问题行 1 行）",
      sum(1 for x in dlg2._rows
          if x["kind"] == "problem" and x["status"] == "待确认") == 1,
      str(statuses(dlg2)))
# 发票问题行未修正 + sheet3 行待补录 → 仍有未处理行 → 硬拦、一行不写
_MB.calls.clear()
n_before2 = len(data2["invoices"])
dlg2.accept()
check("6：仍有未处理行 → accept 硬拦、真实 data 一行不写",
      len(data2["invoices"]) == n_before2 and not data2.get("deferred")
      and len(data2["prepayments"]) == 0,
      f"inv={len(data2['invoices'])} def={data2.get('deferred')} pp={data2['prepayments']}")
check("6：硬拦时给出「尚有未处理的行」提示（正文含「无法入库」）",
      any("无法入库" in c[1] for c in _MB.calls if c[0] == "warning"),
      str(_MB.calls[-1:]))

# ---------------------------------------------------------------- 7) 渲染无告警
data3 = mk_data()
dlg3 = UnifiedImportDialog(data3, "2025-01", STAFF)
dlg3.show()
app.processEvents()
check("窗口可渲染（grab 非空）", not dlg3.grab().isNull())
check("底部汇总含发票数与合计",
      "发票" in dlg3.lbl_summary.text() and "预收款" in dlg3.lbl_summary.text(),
      dlg3.lbl_summary.text())
check("统计胶囊有计数",
      "待补录 1" in dlg3.lbl_stat.text() and "待确认 2" in dlg3.lbl_stat.text()
      and "高置信 1" in dlg3.lbl_stat.text(),
      dlg3.lbl_stat.text())

# ------------------------------------------- 8) 写库前校验（临时库，不碰真实 DB）
import tempfile  # noqa: E402
from pathlib import Path as _P  # noqa: E402

import app.db as _db  # noqa: E402
from app.importer.importer import validate_ledger_before_write  # noqa: E402

_real_db = _db.DB_PATH
try:
    _tmp = _P(tempfile.mkdtemp(prefix="lawfirm_smoke_")) / "t.db"
    _db.DB_PATH = _tmp
    _db.init_db()
    c = _db.get_conn()
    c.execute("INSERT INTO staff (name, staff_type, is_active) VALUES (?,?,1)", ("周立生", "聘用"))
    c.execute("INSERT INTO invoice (invoice_no, invoice_date, total_amount, source) VALUES (?,?,?,?)",
              ("INV-1", "2025-01-15", 6000.0, "import"))
    c.commit()
    c.close()

    d_ok = {"invoices": [{"handlers": [("周立生", 6000.0)]}],
            "sheet_totals": {"sheet1": 6000.0}, "sheet12_total": 6000.0}
    check("校验：合计一致 + 经办人在册 → 通过", validate_ledger_before_write(d_ok, "2025-01") is None)

    d_bad_sum = dict(d_ok, sheet12_total=5000.0)
    err = validate_ledger_before_write(d_bad_sum, "2025-01")
    check("校验：合计不符 → 返回文案", err is not None and "≠" in err, f"got={err!r}")

    d_bad_name = dict(d_ok)
    d_bad_name["invoices"] = [{"handlers": [("查无此人", 6000.0)]}]
    err2 = validate_ledger_before_write(d_bad_name, "2025-01")
    check("校验：经办人不在册 → 返回文案",
          err2 is not None and "不在职工花名册" in err2, f"got={err2!r}")

    # 修正行金额计入后原本失败的合计可通过（回归 sheet12_total 重算的修复）
    check("校验：修正行计入合计后通过（旧逻辑会误判失败）",
          validate_ledger_before_write(d_ok, "2025-01") is None)
finally:
    _db.DB_PATH = _real_db

# ------------------------------------------- 9) 反馈三点回归（只读 / 确认按钮 / sheet1 兜底闸门）
from app.engine.import_confidence import evaluate as _eval  # noqa: E402


def mk_simple(sheet1_empty=True):
    """待确认：sheet1 空备注全额兜底；高置信：sheet1 收款备注齐全。"""
    invs = []
    if sheet1_empty:
        invs.append(mk_inv("25332000000406956789", 6000.0, sheet="sheet1",
                           remark={"receipts": [], "remaining": None, "pure_date": None}))
    invs.append(mk_inv("INV-HI", 1000.0,
                       remark={"receipts": [("2025-01", 0)], "remaining": None, "pure_date": None}))
    return {
        "period": "2025-01", "invoices": invs, "prepayments": [], "problems": [],
        "sheet_totals": {"sheet1": (7000.0 if sheet1_empty else 1000.0)},
        "sheet12_total": (7000.0 if sheet1_empty else 1000.0),
    }


# 引擎层直接验证 confirmed / has_split 闸门（不依赖 GUI）
_e0 = mk_simple()
_before = _eval(_e0, set(STAFF))
check("引擎：sheet1 空备注默认待确认", _before[0]["conf"] == "low", f"reasons={_before[0]['reasons']}")
_after_c = _eval(_e0, set(STAFF), confirmed={0})
check("引擎：confirmed={0} 后该发票变高置信", _after_c[0]["conf"] == "high", f"reasons={_after_c[0]['reasons']}")
import copy as _copy  # noqa: E402
_e2 = _copy.deepcopy(_e0)
_e2["invoices"][0]["split_receipts"] = [("周立生", 4000.0, "2025-01")]
_after_s = _eval(_e2, set(STAFF))
check("引擎：填 split_receipts 亦压制兜底提示", _after_s[0]["conf"] == "high", f"reasons={_after_s[0]['reasons']}")

# 应收账款(sheet3)备注收款日期须落在当月：非当月 → 提示「应收账款非本月收款」
_d_ar = {
    "period": "2025-12",
    "invoices": [mk_inv("AR-1", 5000.0, sheet="sheet3",
                       remark={"receipts": [], "remaining": None, "pure_date": "2025-10-15"})],
    "prepayments": [], "problems": [], "sheet_totals": {"sheet3": 5000.0}, "sheet12_total": 0.0,
}
_e_ar = _eval(_d_ar, set(STAFF))
check("引擎：应收账款收款日期非当月 → 应收账款非本月收款",
      "应收账款非本月收款" in _e_ar[0]["reasons"], f"reasons={_e_ar[0]['reasons']}")
# 当月日期：不报非本月，仍走兜底「请确认」
_d_ar_ok = dict(_d_ar)
_d_ar_ok["invoices"] = [mk_inv("AR-1", 5000.0, sheet="sheet3",
                              remark={"receipts": [], "remaining": None, "pure_date": "2025-12-05"})]
_e_ar_ok = _eval(_d_ar_ok, set(STAFF))
check("引擎：应收账款收款日期为当月 → 不报非本月",
      "应收账款非本月收款" not in _e_ar_ok[0]["reasons"], f"reasons={_e_ar_ok[0]['reasons']}")
check("引擎：应收账款收款日期为当月 → 走兜底请确认",
      "应收账款纯日期按全额收款，请确认" in _e_ar_ok[0]["reasons"], f"reasons={_e_ar_ok[0]['reasons']}")

# 点2：待确认行「确认」按钮 → 移出待确认、归入已确认/高置信
d2 = mk_simple()
d2dlg = UnifiedImportDialog(d2, "2025-01", STAFF)
d2dlg.show()
low = next(r for r in d2dlg._rows if r["kind"] == "invoice" and r["inv_idx"] == 0)
_show_all(d2dlg)          # 4-2：默认筛选=待补录，本用例无待补录行 → 先切「全部」
_select(d2dlg, low)
check("点2：待确认行显示「确认」按钮", d2dlg.btn_confirm_row.isVisible())
check("点2：待确认行不显示「编辑」按钮", not d2dlg.btn_edit.isVisible())
d2dlg._confirm_row()
low2 = next(r for r in d2dlg._rows if r["kind"] == "invoice" and r["inv_idx"] == 0)
check("点2：确认后该发票变高置信", low2["ev"]["conf"] == "high", f"reasons={low2['ev']['reasons']}")
check("点2：确认后离开待确认（状态=高置信）", low2["status"] == "高置信", f"status={low2['status']}")
check("点2：确认后归入已确认类别", low2["is_confirmed"] is True and low2["status"] == "高置信",
      f"status={low2['status']} confirmed={low2['is_confirmed']}")

# 点3：填收款金额+日期并保存 → 不再是待确认（即便未全额收款）
d3 = mk_simple()
d3dlg = UnifiedImportDialog(d3, "2025-01", STAFF)
d3dlg.show()
low3 = next(r for r in d3dlg._rows if r["kind"] == "invoice" and r["inv_idx"] == 0)
_show_all(d3dlg)          # 4-2：同上，先切「全部」才能选到该行
_select(d3dlg, low3)
pp3 = d3dlg.fix_panel
pp3.htable.item(0, 2).setText("4000.00")   # 未全额收款
pp3.htable.item(0, 3).setText("2025-01")
d3dlg._inv_edits[0] = pp3.read_fix()
d3dlg._rebuild()
low3b = next(r for r in d3dlg._rows if r["kind"] == "invoice" and r["inv_idx"] == 0)
check("点3：填收款并保存 → 不再是待确认", low3b["ev"]["conf"] == "high", f"reasons={low3b['ev']['reasons']}")
check("点3：未全额收款也离开待确认", low3b["status"] == "高置信", f"status={low3b['status']}")

# 点1：高置信默认只读，点「编辑」才解锁（高置信不在默认「待补录」筛选，先切「全部」）
d1 = mk_simple()
d1dlg = UnifiedImportDialog(d1, "2025-01", STAFF)
d1dlg.show()
hi = next(r for r in d1dlg._rows if r["kind"] == "invoice" and r["inv_idx"] == 1)
_show_all(d1dlg)
_select(d1dlg, hi)
check("点1：高置信默认只读（开票日期）", d1dlg.fix_panel.inv_date.isReadOnly())
check("点1：高置信默认只读（经办人下拉禁用）", not d1dlg.fix_panel.htable.cellWidget(0, 0).isEnabled())
check("点1：高置信默认只读（保存按钮隐藏）", not d1dlg.btn_save.isVisible())
check("点1：高置信显示「编辑」按钮", d1dlg.btn_edit.isVisible())
d1dlg._enter_edit_mode()
check("点1：点编辑后开票日期可写", not d1dlg.fix_panel.inv_date.isReadOnly())
check("点1：点编辑后经办人下拉启用", d1dlg.fix_panel.htable.cellWidget(0, 0).isEnabled())
check("点1：点编辑后保存按钮出现", d1dlg.btn_save.isVisible())
check("点1：点编辑后编辑按钮隐藏", not d1dlg.btn_edit.isVisible())
# 编辑后保存 → 高置信发票归入已确认（已确认 ⊂ 高置信）
d1dlg.fix_panel.inv_amount.setText("1000")
d1dlg._inv_edits[1] = d1dlg.fix_panel.read_fix()
d1dlg._rebuild()
hi_b = next(r for r in d1dlg._rows if r["kind"] == "invoice" and r["inv_idx"] == 1)
check("点1：编辑并保存后高置信发票归入已确认", hi_b["is_confirmed"] is True and hi_b["status"] == "高置信",
      f"status={hi_b['status']} confirmed={hi_b['is_confirmed']}")

# ------------------------------------------- 10) 阶段 2-2：A3 应收账款行就地编辑
from app.importer.ledger_import import split_deferred as _split_def  # noqa: E402


def mk_ar(no, total, handlers, remark, row_no=20, date="2024-11-05"):
    """构造一条 sheet3（应收账款）解析行（与 _parse_invoice_sheet 同结构）。"""
    txt = "、".join(f"{n}{a:g}" for n, a in handlers)
    return {
        "sheet": "sheet3", "sheet_name": "应收账款", "row_no": row_no,
        "header": HEADER, "raw_row": [no, "应收公司", str(total), txt, ""],
        "invoice_no": no, "invoice_date": date, "buyer": "应收公司",
        "total_amount": total, "handlers": handlers, "handler_text": txt,
        "remark_raw": "", "remark": remark, "case_no": "", "is_red": False,
        "split_receipts": [],
    }


def mk_ar_data(ar):
    """把应收账款行放进 invoices（由 split_deferred 移入 deferred，模拟解析期切分）。"""
    return {
        "period": "2025-01",
        "invoices": [ar, mk_inv("INV-HI", 1000.0,
                                remark={"receipts": [("2025-01", 0)],
                                        "remaining": None, "pure_date": None})],
        "prepayments": [], "problems": [], "deferred": [],
        "sheet_totals": {"sheet3": ar["total_amount"], "sheet1": 1000.0},
        "sheet12_total": 1000.0,
    }


def _deferred_row(dlg):
    return next(r for r in dlg._rows if r["kind"] == "deferred")


def _htable_rows(panel):
    t = panel.htable
    out = []
    for i in range(t.rowCount()):
        name = t.cellWidget(i, 0).currentText()
        bill = t.item(i, 1).text() if t.item(i, 1) else ""
        recv = t.item(i, 2).text() if t.item(i, 2) else ""
        date = t.item(i, 3).text() if t.item(i, 3) else ""
        out.append((name, bill, recv, date))
    return out


# ---- 10a) 预填：单期（纯日期备注 = 收款日 / 全额）----
_LIB.clear()
PURE = {"receipts": [], "remaining": None, "pure_date": "2025-01-20"}
ar1 = mk_ar("AR-1", 5000.0, [("周立生", 5000.0)], PURE)
d10 = mk_ar_data(ar1)
a = UnifiedImportDialog(d10, "2025-01", STAFF)
a.show()
ra = _deferred_row(a)
check("A3：应收账款行预填单期一行（含收款金额与日期）",
      _htable_rows(a.fix_panel) == [("周立生", "5000.0", "5,000.00", "2025-01")],
      str(_htable_rows(a.fix_panel)))
check("A3：右键面板为发票表单且可编辑（非只读）",
      a.fix_panel.inv_box.isVisible() and not a.fix_panel.inv_date.isReadOnly())
check("A3：应收账款行显示「保存修改」；待补录行暂不显示「确认」（阶段 4-2）",
      a.btn_save.isVisible() and not a.btn_confirm_row.isVisible())
check("A1：汇总行显示「需补录原票 N 张」并按来源拆分（阶段 5）",
      "需补录原票 1 张（应收账款 1 · 红字引用 0）" in a.lbl_summary.text(), a.lbl_summary.text())

# ---- 10b) 就地修改 → 回写 data["deferred"]；需补录行不置 receipt_confirmed ----
a.fix_panel.htable.item(0, 2).setText("3,000.00")
a.fix_panel.htable.item(0, 3).setText("2025-02")
a._save_fix()
check("A3：编辑回写工作副本 deferred（金额/日期已改）",
      a._work["deferred"][0]["split_receipts"] == [("周立生", 3000.0, "2025-02")],
      str(a._work["deferred"][0]["split_receipts"]))
check("A3：真实 data 未被修改（deferred 仍为空 / 行仍在 invoices）",
      d10["deferred"] == []
      and [x["invoice_no"] for x in d10["invoices"]] == ["AR-1", "INV-HI"],
      f"deferred={d10['deferred']} invoices={[x['invoice_no'] for x in d10['invoices']]}")
ra2 = _deferred_row(a)
check("A3：就地编辑后仍留在「待补录」（要补录原票才转「待确认」，阶段 4-2）",
      ra2["status"] == "待补录", f"status={ra2['status']}")
m10 = a._merged_data()
check("A3：需补录原票行**不**置 receipt_confirmed（本页不出收款）",
      not m10["deferred"][0].get("receipt_confirmed"),
      str(m10["deferred"][0].get("receipt_confirmed")))
check("A3：需补录原票行仍写 need_backfill=True（送补录页用）",
      m10["deferred"][0].get("need_backfill") is True)
items10 = a.collect_import_fixes()
check("A3：应收账款行编辑计入导入留痕（kind=edit / 票号正确）",
      any(it["kind"] == "edit" and it["invoice_no"] == "AR-1" for it in items10),
      str([(it["kind"], it["invoice_no"]) for it in items10]))

# ---- 10c) 已在库行：「确认」→ 采纳台账收款口径 + receipt_confirmed=True ----
_LIB.add("AR-2")
ar2 = mk_ar("AR-2", 5000.0, [("周立生", 5000.0)], PURE)
d10c = mk_ar_data(ar2)
c = UnifiedImportDialog(d10c, "2025-01", STAFF)
c.show()
rc = _deferred_row(c)
_show_all(c)              # 4-2：已入库行落「待确认」，默认「待补录」视图里看不到 → 切「全部」
_select(c, rc)            # 并显式选中，否则右栏读的是旧行、`_confirm_row` 也作用于旧行
check("A2：票号已在库 → 已入库，请确认收款",
      rc["deferred"].get("need_backfill") is False
      and c._row_field(rc, "reason") == "已入库，请确认收款",
      c._row_field(rc, "reason"))
check("A3：已入库行预填同一口径（全额 / 2025-01）",
      _htable_rows(c.fix_panel) == [("周立生", "5000.0", "5,000.00", "2025-01")],
      str(_htable_rows(c.fix_panel)))
check("A1：汇总行显示「已入库待确认收款 N 张」",
      "应收账款已入库待确认收款 1 张" in c.lbl_summary.text(), c.lbl_summary.text())
c._confirm_row()
rc2 = _deferred_row(c)
check("A3：已入库行确认后离开待确认且归入已确认",
      rc2["status"] == "高置信" and rc2.get("is_confirmed") is True,
      f"status={rc2['status']} confirmed={rc2.get('is_confirmed')}")
_mb_before = len(_MB.calls)
c._confirm_row()   # 再点一次不应弹「需补录」提示（已在库行静默）
check("A3：已入库行确认不弹「需补录原票」提示", len(_MB.calls) == _mb_before,
      str(_MB.calls[_mb_before:]))
m10c = c._merged_data()
check("A3/A10：已入库行确认后置 receipt_confirmed（入库时追加收款）",
      m10c["deferred"][0].get("receipt_confirmed") is True,
      str(m10c["deferred"][0].get("receipt_confirmed")))
check("A3/A10：确认采纳台账预填收款明细（全额 2025-01）",
      m10c["deferred"][0]["split_receipts"] == [("周立生", 5000.0, "2025-01")],
      str(m10c["deferred"][0]["split_receipts"]))
check("A3：确认不动真实 data（deferred 仍为空 / invoices 未变）",
      d10c["deferred"] == []
      and [x["invoice_no"] for x in d10c["invoices"]] == ["AR-2", "INV-HI"],
      f"deferred={d10c['deferred']}")

# ---- 10d) 需补录行点「确认」→ 有可读提示，且不出收款 ----
_LIB.discard("AR-2")
d10d = mk_ar_data(mk_ar("AR-3", 5000.0, [("周立生", 5000.0)], PURE))
e = UnifiedImportDialog(d10d, "2025-01", STAFF)
e.show()
re_d = _deferred_row(e)
_show_all(e)
_select(e, re_d)
_MB.calls.clear()
e._confirm_row()
_tips = [t for _k, t in _MB.calls]
check("A3：需补录行确认弹出可读提示（不写任何数据）",
      any("补录原票" in t and "不会写入任何数据" in t for t in _tips), str(_tips))
m10d = e._merged_data()
check("A3：需补录行确认后仍不置 receipt_confirmed",
      not m10d["deferred"][0].get("receipt_confirmed"))
check("A3：需补录行确认后 split_receipts 不被写入",
      not m10d["deferred"][0].get("split_receipts"),
      str(m10d["deferred"][0].get("split_receipts")))

# ---- 10e) A11 同源：多期收款展开为「同名多行」，合计仍等于开票总额 ----
MP = {"receipts": [("2025-08", 30000.0), ("2025-10", 30000.0)],
      "remaining": None, "pure_date": None}
mp = mk_ar("MP-1", 60000.0, [("周立生", 30000.0), ("陈娟", 30000.0)], MP, date="2024-03-01")
d10e = mk_ar_data(mp)
_split_def(d10e, "2025-01", _LIB)   # 模拟解析期已切分（_orig_data 自带 deferred）
f = UnifiedImportDialog(d10e, "2025-01", STAFF)
f.show()
rf = _deferred_row(f)
_rows_mp = _htable_rows(f.fix_panel)
check("A11：多期收款展开为 4 行（2 人 × 2 期）", len(_rows_mp) == 4, str(_rows_mp))
check("A11：每人首行填开票分摊、其余行 0（合计=开票总额）",
      [r[1] for r in _rows_mp] == ["30000.0", "0.0", "30000.0", "0.0"],
      str([r[1] for r in _rows_mp]))
check("A11：每行收款=该期金额×份额、日期=该期 YYYY-MM",
      [r[2] for r in _rows_mp] == ["15,000.00"] * 4
      and [r[3] for r in _rows_mp] == ["2025-08", "2025-10", "2025-08", "2025-10"],
      str(_rows_mp))
check("A11：收款认定按年月归集（两期各 30000）",
      f._row_field(rf, "receipt") == "2025-08 30,000.00、2025-10 30,000.00",
      f._row_field(rf, "receipt"))
check("A11：「各经办人已收」按人合计",
      f._deferred_recv_text(rf["deferred"]) == "周立生 30,000.00、陈娟 30,000.00",
      f._deferred_recv_text(rf["deferred"]))
f._save_fix()   # 直接确认台账预填（不改）→ 读回后应无损
split_mp = f._work["deferred"][0]["split_receipts"]
check("A11：保存后多期收款无损保留（4 条 / 合计 60000）",
      len(split_mp) == 4 and abs(sum(x[1] for x in split_mp) - 60000.0) < 0.01,
      str(split_mp))
check("A11：保存后开票分摊合并回 2 人（合计 60000）",
      f._work["deferred"][0]["handlers"] == [("周立生", 30000.0), ("陈娟", 30000.0)],
      str(f._work["deferred"][0]["handlers"]))
_items_mp = [it for it in f.collect_import_fixes()
             if it["kind"] == "edit" and it["invoice_no"] == "MP-1"]
check("A3：留痕旧值取原始 deferred 行（开票日期未被误判为变化）",
      _items_mp and _items_mp[0]["old"].get("invoice_date") == "2024-03-01"
      and _items_mp[0]["new"].get("invoice_date") == "2024-03-01",
      str(_items_mp[:1]))
check("A3：留痕 new 侧带 4 条多期收款",
      _items_mp and len(_items_mp[0]["new"].get("split_receipts") or []) == 4,
      str(_items_mp and _items_mp[0]["new"].get("split_receipts")))

# ---- 10f) 未处理应收账款行：确认入库前**硬拦**（不静默、不允许跳过）----
_MB.calls.clear()
_LIB.add("AR-4")
d10f = mk_ar_data(mk_ar("AR-4", 8000.0, [("周立生", 8000.0)], PURE))
g = UnifiedImportDialog(d10f, "2025-01", STAFF)
g.show()
check("A2：已在库行 reason = 已入库，请确认收款",
      g._row_field(_deferred_row(g), "reason") == "已入库，请确认收款")
g.accept()
_warns = [c[1] for c in _MB.calls if c[0] == "warning"]
check("A1：存在未处理的应收账款行时 accept 硬拦（不静默）",
      any("无法入库" in t for t in _warns), str(_MB.calls[-3:]))
check("A1：硬拦文案说明该「已入库」行要先确认收款",
      any("待确认" in t and "已入库，请确认收款" in t for t in _warns), str(_warns[:1]))
check("A1：硬拦 → 未确认收款的已入库行不置 receipt_confirmed（不误写）",
      not (d10f.get("deferred") or [{}])[0].get("receipt_confirmed"),
      str((d10f.get("deferred") or [{}])[0].get("receipt_confirmed")))
_LIB.discard("AR-4")


# ------------------------------------------- 11) 阶段 3：行内「补录原票」（B2g/B2h/B2c/B2d）
# 本冒烟不碰真实 DB：get_conn / prefill_red_original / load_invoice_detail 全打桩，
# BackfillDialog 换桩（记录构造参数 + 可指定返回值），只验证**复核页自身**的行为。
_RED: dict = {}        # 红字票号 → 其引用原票号（_red_orig_no 直查）
_LIB_DB: set = set()   # _invoice_in_library 直查的「已在库」票号


def _set_in_lib(no, flag):
    """同时改两处「已在库」口径：`_LIB`（驱动 need_backfill 的 library_invoice_nos）
    与 `_LIB_DB`（面板按钮直查的 invoice 表）。真实运行时两者同源（同一个库）。"""
    (_LIB.add if flag else _LIB.discard)(no)
    (_LIB_DB.add if flag else _LIB_DB.discard)(no)


class _Cur11:
    def __init__(self, one=None):
        self._one = one

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []

    def __iter__(self):
        return iter(())


class _Conn11:
    """极简连接桩：只为 `_red_orig_no` / `_invoice_in_library` 两条查询服务。"""

    def execute(self, sql, params=()):
        s = " ".join(str(sql).lower().split())
        p = tuple(params or ())
        if "orig_invoice_no from invoice" in s:
            red = p[0] if p else ""
            return _Cur11({"orig_invoice_no": _RED[red]} if red in _RED else None)
        if s.startswith("select 1 from invoice"):
            return _Cur11({"1": 1} if (p[0] if p else "") in _LIB_DB else None)
        return _Cur11(None)

    def close(self):
        pass


U.get_conn = lambda: _Conn11()
U.load_invoice_detail = lambda no: {
    "invoice_no": no, "invoice_date": "2024-01-01", "buyer": "库中购方",
    "total_amount": 900.0, "handlers": [{"name": "周立生", "billing": 900.0}]}
U.prefill_red_original = lambda conn, no: {
    "invoice_no": no, "invoice_date": "", "buyer": "红字参考购方",
    "total_amount": 7777.0,
    "handlers": [{"name": "库中某人", "billing": 7777.0, "received": 0.0, "date": ""}]}


class _FakeBF:
    """`BackfillDialog` 桩：`payload` = 确定返回的数据；None = 用户取消。

    `seen` 逐次记录构造参数（title / readonly / prefill），供断言「弹的是补录还是查看」。
    """
    payload = None
    seen: list = []

    def __init__(self, prefill=None, *, title="", locked_no=False,
                 readonly=False, validator=None, soft_check=None, parent=None):
        type(self).seen.append({"title": title, "readonly": readonly,
                                "locked_no": locked_no, "validator": validator,
                                "soft_check": soft_check,
                                "prefill": dict(prefill or {})})

    def exec(self):
        return (U.QDialog.DialogCode.Accepted
                if type(self).payload is not None
                else U.QDialog.DialogCode.Rejected)

    def data(self):
        return dict(type(self).payload or {})


U.BackfillDialog = _FakeBF


def _red_row(dlg):
    return next(r for r in dlg._rows
                if r["kind"] == "invoice" and r["ev"]["is_red"])


def _plain_row(dlg):
    return next(r for r in dlg._rows
                if r["kind"] == "invoice" and not r["ev"]["is_red"])


def mk_red_data():
    """含一张红字发票（引用 ORIG-9）与一张普通高置信发票。"""
    red = mk_inv("RED-1", -3000.0, sheet="sheet1",
                 remark={"receipts": [], "remaining": None, "pure_date": None})
    red["is_red"] = True
    return {
        "period": "2025-01",
        "invoices": [red, mk_inv("INV-HI", 1000.0,
                                 remark={"receipts": [("2025-01", 0)],
                                         "remaining": None, "pure_date": None})],
        "prepayments": [], "problems": [], "deferred": [],
        "sheet_totals": {"sheet1": -2000.0}, "sheet12_total": -2000.0,
    }


BF_PAYLOAD = {
    "invoice_no": "AR-B1", "invoice_date": "2024-11-05", "buyer": "应收公司",
    "total_amount": 5000.0,
    "handlers": [{"name": "周立生", "billing": 5000.0,
                  "received": 0.0, "date": "", "date_raw": ""}],
}

# ---- 11a) 按钮可见性：需补录行有、普通发票行无 ----
_RED.clear(); _LIB_DB.clear(); _LIB.clear()
_FakeBF.payload = None; _FakeBF.seen.clear()
b1_src = mk_ar_data(mk_ar("AR-B1", 5000.0, [("周立生", 5000.0)], PURE))
b1 = UnifiedImportDialog(b1_src, "2025-01", STAFF)
b1.show()
_show_all(b1)
_select(b1, _deferred_row(b1))
check("B2g：需补录行显示「补录原票」按钮",
      b1.btn_backfill.isVisible() and b1.btn_backfill.text() == "补录原票",
      f"vis={b1.btn_backfill.isVisible()} text={b1.btn_backfill.text()}")
_select(b1, _plain_row(b1))
check("B2g：普通发票行不显示补录按钮", not b1.btn_backfill.isVisible())

# ---- 11b) 确定 → 收集（不写库）+ 按钮翻转 + 原因四态 + 计数同步 ----
_select(b1, _deferred_row(b1))
_FakeBF.seen.clear()
_FakeBF.payload = dict(BF_PAYLOAD)
b1._open_backfill()
check("B2c：确定后收集进面板（不立即写库）", "AR-B1" in b1._backfills,
      str(b1._backfills))
check("B2c：条目 create=True（复核页只产出新增口径）",
      (b1._backfills.get("AR-B1") or {}).get("create") is True,
      str(b1._backfills.get("AR-B1")))
check("B2g：弹出的是「补录原票」且非只读",
      _FakeBF.seen and _FakeBF.seen[-1]["title"] == "补录原票"
      and _FakeBF.seen[-1]["readonly"] is False, str(_FakeBF.seen[-1:]))
check("B2g：补录弹窗挂了写前校验（validator 非空）",
      _FakeBF.seen and _FakeBF.seen[-1]["validator"] is not None)
check("B2g：预填取自应收账款行（票号 / 金额）",
      _FakeBF.seen
      and _FakeBF.seen[-1]["prefill"].get("invoice_no") == "AR-B1"
      and abs((_FakeBF.seen[-1]["prefill"].get("total_amount") or 0) - 5000.0) < 0.01,
      str(_FakeBF.seen[-1]["prefill"]))
rb = _deferred_row(b1)
_show_all(b1)
_select(b1, rb)
check("阶段4-2：补录后该行离开「待补录」、落到「待确认」（不再直接高置信）",
      rb["status"] == "待确认" and not b1._match(rb, "待补录")
      and b1._match(rb, "待确认"), rb["status"])
check("阶段4-2：补录后按钮翻转为「修改补录」（D 甲）",
      b1.btn_backfill.isVisible() and b1.btn_backfill.text() == "修改补录",
      f"vis={b1.btn_backfill.isVisible()} text={b1.btn_backfill.text()}")
check("阶段4-2：补录后原因列 = 已补录，请确认收款",
      b1._row_field(rb, "reason") == U.REASON_BACKFILLED,
      b1._row_field(rb, "reason"))
check("B2h：补录后不再计入「需补录原票」计数",
      "需补录原票" not in b1.lbl_summary.text(), b1.lbl_summary.text())
check("阶段4-2：底部出现「已补录待确认收款 1 张」",
      "已补录待确认收款 1 张" in b1.lbl_summary.text(), b1.lbl_summary.text())
check("阶段4-2：底部出现「本次已填补录 1 张」",
      "本次已填补录 1 张" in b1.lbl_summary.text(), b1.lbl_summary.text())
check("阶段4-2：补录后该行**尚未**确认（要等「确认」才离开待确认）",
      rb.get("is_confirmed") is False)
m11 = b1._merged_data()
check("B2c：merged['backfills'] 携带行内补录条目",
      [x.get("invoice_no") for x in (m11.get("backfills") or [])] == ["AR-B1"],
      str(m11.get("backfills")))
check("B2c：merged 中该行仍 need_backfill=True（送补录页口径不变）",
      m11["deferred"][0].get("need_backfill") is True)
check("B2c：需补录行不置 receipt_confirmed（本页绝不出收款）",
      not m11["deferred"][0].get("receipt_confirmed"))
check("B2c：补录不动调用方的真实 data（deferred 仍空 / 无 backfills）",
      b1_src["deferred"] == [] and not b1_src.get("backfills"),
      f"deferred={b1_src['deferred']} bf={b1_src.get('backfills')}")

# ---- 11b-2) A 甲：在「待补录」里填的收款 → 落到「待确认」并**回显** ----
# 红冲导致的补录尤其要收款信息；此处填了收款后再回显，用户才好在待确认里直接点确认。
_LIB.clear(); _LIB_DB.clear()
b7 = UnifiedImportDialog(
    mk_ar_data(mk_ar("AR-B1", 5000.0, [("周立生", 5000.0)], PURE)),
    "2025-01", STAFF)
b7.show()
_show_all(b7)
_select(b7, _deferred_row(b7))
_FakeBF.payload = {
    "invoice_no": "AR-B1", "invoice_date": "2024-11-05", "buyer": "应收公司",
    "total_amount": 5000.0,
    "handlers": [{"name": "周立生", "billing": 5000.0,
                  "received": 5000.0, "date": "2025-01", "date_raw": "2025-01"}],
}
b7._open_backfill()
r7 = _deferred_row(b7)
_show_all(b7)
_select(b7, r7)
check("A 甲：待补录中填的收款 → 该行转「待确认」并回显到右栏预填",
      r7["status"] == "待确认"
      and _htable_rows(b7.fix_panel) == [("周立生", "5000.0", "5,000.00", "2025-01")],
      f"status={r7['status']} rows={_htable_rows(b7.fix_panel)}")
check("A 甲：回显到「各经办人已收」列",
      "5,000.00" in b7._recv_text(r7), b7._recv_text(r7))
_m7 = b7._merged_data()
_bf7 = next((x for x in (_m7.get("backfills") or [])
             if x.get("invoice_no") == "AR-B1"), {})
check("A 甲：回显的收款随 backfills 落库（handlers.received / date）",
      [(h.get("name"), h.get("received"), h.get("date"))
       for h in (_bf7.get("handlers") or [])] == [("周立生", 5000.0, "2025-01")],
      str(_bf7))
check("A 甲：回显后仍不置 receipt_confirmed（要显式点「确认」）",
      not _m7["deferred"][0].get("receipt_confirmed"),
      str(_m7["deferred"][0].get("receipt_confirmed")))

# ---- 11c) 取消 → 面板不收集（一行不写） ----
_FakeBF.payload = None
b2 = UnifiedImportDialog(
    mk_ar_data(mk_ar("AR-B2", 5000.0, [("周立生", 5000.0)], PURE)),
    "2025-01", STAFF)
b2.show()
_show_all(b2)
_select(b2, _deferred_row(b2))
b2._open_backfill()
check("B2c：取消 → 面板不收集（一行不写）", b2._backfills == {}, str(b2._backfills))
check("B2c：取消后按钮仍为「补录原票」", b2.btn_backfill.text() == "补录原票",
      b2.btn_backfill.text())
check("B2c：取消后底部无「已填补录」计数",
      "已填补录" not in b2.lbl_summary.text(), b2.lbl_summary.text())
check("B2c：取消后仍计入「需补录原票 1 张」（来源拆分：应收账款 1）",
      "需补录原票 1 张（应收账款 1 · 红字引用 0）" in b2.lbl_summary.text(), b2.lbl_summary.text())
check("B2c：取消 → merged 无 backfills", not (b2._merged_data().get("backfills")))

# ---- 11d) 票已在库且未填过 → 只读「查看原票」（不收集） ----
_set_in_lib("AR-B3", True)
_FakeBF.seen.clear()
b3 = UnifiedImportDialog(
    mk_ar_data(mk_ar("AR-B3", 5000.0, [("周立生", 5000.0)], PURE)),
    "2025-01", STAFF)
b3.show()
_show_all(b3)
_select(b3, _deferred_row(b3))
check("B2g：已在库行按钮文案 = 查看原票", b3.btn_backfill.text() == "查看原票",
      b3.btn_backfill.text())
check("B2g：已在库行原因 = 已入库，请确认收款",
      b3._row_field(_deferred_row(b3), "reason") == "已入库，请确认收款",
      b3._row_field(_deferred_row(b3), "reason"))
b3._open_backfill()
check("B2g：已在库且未填过 → 弹只读「查看原票」",
      _FakeBF.seen and _FakeBF.seen[-1]["readonly"] is True
      and _FakeBF.seen[-1]["title"] == "查看原票", str(_FakeBF.seen[-1:]))
check("B2g：只读查看不收集补录", b3._backfills == {})
_set_in_lib("AR-B3", False)

# ---- 11e) 红字行：补的是「其引用原票」，票号经 _red_orig_no 反查 ----
_RED["RED-1"] = "ORIG-9"
b4 = UnifiedImportDialog(mk_red_data(), "2025-01", STAFF)
b4.show()
_show_all(b4)
_select(b4, _red_row(b4))
check("B2g：红字行按钮指向其引用原票（补录原票）",
      b4.btn_backfill.isVisible() and b4.btn_backfill.text() == "补录原票",
      f"vis={b4.btn_backfill.isVisible()} text={b4.btn_backfill.text()}")
_FakeBF.seen.clear()
_FakeBF.payload = {
    "invoice_no": "ORIG-9", "invoice_date": "2024-06-01", "buyer": "红字参考购方",
    "total_amount": 3000.0,
    "handlers": [{"name": "周立生", "billing": 3000.0,
                  "received": 0.0, "date": "", "date_raw": ""}],
}
b4._open_backfill()
check("B2c：红字行补录收集的是「引用原票」号（不是红字票号）",
      "ORIG-9" in b4._backfills and "RED-1" not in b4._backfills,
      str(list(b4._backfills)))
check("B2g：红字行预填取**本行红字**（票号=原票；金额/经办人取绝对值，不查库）",
      _FakeBF.seen
      and _FakeBF.seen[-1]["prefill"].get("invoice_no") == "ORIG-9"
      and _FakeBF.seen[-1]["prefill"].get("total_amount") == 3000.0
      and [(h.get("name"), h.get("billing"))
           for h in (_FakeBF.seen[-1]["prefill"].get("handlers") or [])] == [("周立生", 3000.0)],
      str(_FakeBF.seen[-1:]))
check("B2g：复核页行内补录锁定票号 + 挂红蓝一致性软校验",
      _FakeBF.seen and _FakeBF.seen[-1]["locked_no"] is True
      and callable(_FakeBF.seen[-1].get("soft_check")), str(_FakeBF.seen[-1:]))
# 原票已在库 → 红字行只读查看
_set_in_lib("ORIG-9", True)
b5 = UnifiedImportDialog(mk_red_data(), "2025-01", STAFF)
b5.show()
_show_all(b5)
_select(b5, _red_row(b5))
check("B2g：原票已在库 → 红字行按钮变「查看原票」", b5.btn_backfill.text() == "查看原票",
      b5.btn_backfill.text())
_set_in_lib("ORIG-9", False)
# 反查不到原票号 → 无入口（按钮隐藏）
_RED.clear()
b6 = UnifiedImportDialog(mk_red_data(), "2025-01", STAFF)
b6.show()
_show_all(b6)
_select(b6, _red_row(b6))
check("B2g：红字行取不到原票号 → 无补录入口（按钮隐藏）",
      not b6.btn_backfill.isVisible())

# ---- 11f) 写前校验（B2d）与「本次已填过同票号」（B2e） ----
_orig_validator = U.backfill_validator
U.backfill_validator = lambda d: (None if (d.get("total_amount") or 0) > 0
                                  else "价税合计必须大于 0")
check("B2d：票号未填过 + 校验通过 → 放行",
      b1._validate_backfill({"invoice_no": "NEW-1", "total_amount": 1000.0}) is None)
_err_dup = b1._validate_backfill({"invoice_no": "AR-B1", "total_amount": 1000.0})
check("B2e：本次已填过同票号 → 拦下并给出可读提示",
      bool(_err_dup) and "AR-B1" in _err_dup, str(_err_dup))
check("B2e：编辑同一票号（editing_no 相同）→ 放行",
      b1._validate_backfill({"invoice_no": "AR-B1", "total_amount": 1000.0},
                            editing_no="AR-B1") is None)
check("B2d：写前校验不过 → 原样返回错误文案",
      b1._validate_backfill({"invoice_no": "NEW-2", "total_amount": 0.0})
      == "价税合计必须大于 0")
U.backfill_validator = _orig_validator


# ------------------------------------------- 12) 批 2：「台账 ⇄ 库」比对判进四态
# 导入前把旧「导入后」页的台账⇄库比对判进四态：命中 → 待确认；一致 → 零扰动。
# **口径（2026-09-18 用户拍板方案甲，实测见 docs/…batch0…md §18）**：
#   ① 只比「**票已在库**」的行 —— 票不在库 = 本批正要写入它（当期 sheet1/2 全量如此），
#      不是差异（旧写法全报「库中缺失，请确认」→ 当期数据把待确认刷满）；
#   ② 只比「**金额**」维度 —— 「票在库」≠「库侧有可比数据」：`charge_detail` /
#      `collection` 正是**本次台账**要写的，导入那一刻必然为空 ⇒ 比它们 = 拿「缺数据」
#      当「不一致」（实测真实库 2025-01：三维全比 86/86 全中，只比金额 0 中）。
#      这也是批 0 §5.5 / §6 早已给出的结论（已收认定假报 78.2%）。
# 引擎层用**伪造 lib 上下文**（不碰 DB）；对话框层用**临时库**走真 build_lib_context。
_FULL_R = {"receipts": [("2025-01", 0)], "remaining": None, "pure_date": None}


def _lib_ctx2(invs=None, cd=None, act=None):
    """伪造 build_lib_context 产物（snap 恒空 → 走 live 口径）。"""
    return {"period": "2025-01", "inv": invs or {}, "cd": cd or {},
            "act": act or {}, "snap": {}}


def _inv2(no="L1", total=1000.0):
    return mk_inv(no, total, sheet="sheet1", remark=_FULL_R)


# 12-1 完全一致 → 零扰动（保持高置信、无台账⇄库原因）
_eA = {"period": "2025-01", "invoices": [_inv2()], "prepayments": [], "problems": [],
       "sheet_totals": {"sheet1": 1000.0}, "sheet12_total": 1000.0}
_rA = _eval(_eA, set(STAFF), period="2025-01",
            lib=_lib_ctx2({"L1": {"buyer": "某某公司", "total_amount": 1000.0}},
                          {"L1": {"周立生": 1000.0}},
                          {"L1": [("2025-01", 1000.0, "周立生")]}))[0]
check("批2：台账⇄库完全一致 → 不加原因、保持高置信（零扰动）",
      _rA["conf"] == "high" and not any("台账⇄库" in x for x in _rA["reasons"]),
      f"reasons={_rA['reasons']}")

# 12-2 金额不符 → 待确认，原因带维度名与两侧对照值
_eB = _copy.deepcopy(_eA)
_rB = _eval(_eB, set(STAFF), period="2025-01",
            lib=_lib_ctx2({"L1": {"buyer": "某某公司", "total_amount": 800.0}},
                          {"L1": {"周立生": 1000.0}},
                          {"L1": [("2025-01", 1000.0, "周立生")]}))[0]
check("批2：金额不符 → 待确认 + 「台账⇄库不一致（金额）」+ 对照值",
      _rB["conf"] == "low"
      and any(U.REASON_LIB_DIFF.format("金额") in x
              and "金额 台账1,000.00 ⇄ 库800.00" in x for x in _rB["reasons"]),
      f"reasons={_rB['reasons']}")

# 12-3 库中缺失 → **不比不报**（方案 B：票不在库 = 本批正要写入它，不是差异）
# 旧写法会判待确认 + 报「库中缺失（台账1,000.00 ⇄ 库 —）」→ 当期票全量命中 →
# 待确认里几乎全是当期数据。与 12-2（票在库且金额不符 → 必报）构成正反对照。
_eC = _copy.deepcopy(_eA)
_rC = _eval(_eC, set(STAFF), period="2025-01", lib=_lib_ctx2())[0]
check("批2：库中缺失 → 不比不报、保持高置信（方案 B：只比「票已在库」的行）",
      _rC["conf"] == "high" and not any("台账⇄库" in x for x in _rC["reasons"]),
      f"reasons={_rC['reasons']}")

# 12-3b 库上下文非空、但**本票号**不在其中 → 仍不报（当期 sheet1/2 全量的真实形态）
_eCb = _copy.deepcopy(_eA)
_rCb = _eval(_eCb, set(STAFF), period="2025-01",
             lib=_lib_ctx2({"L9": {"buyer": "某某公司", "total_amount": 800.0}},
                           {"L9": {"周立生": 800.0}}))[0]
check("批2：库非空但本票不在库 → 不比不报（当期数据成群误报的根因已除）",
      _rCb["conf"] == "high" and not any("台账⇄库" in x for x in _rCb["reasons"]),
      f"reasons={_rCb['reasons']}")

# 12-4 「经办人分摊」维度**退出导入前比对**：库侧有 cd 且明显不符 → 仍不比不报
_eD = _copy.deepcopy(_eA)
_rD = _eval(_eD, set(STAFF), period="2025-01",
            lib=_lib_ctx2({"L1": {"buyer": "某某公司", "total_amount": 1000.0}},
                          {"L1": {"周立生": 600.0, "陈娟": 400.0}},
                          {"L1": [("2025-01", 1000.0, "周立生")]}))[0]
check("批2：库侧分摊不符也不比不报（分摊维度退出导入前比对，方案甲）",
      _rD["conf"] == "high" and not any("台账⇄库" in x for x in _rD["reasons"]),
      f"reasons={_rD['reasons']}")

# 12-5 「已收认定」维度同样退出（库侧无收款 → 也不能报）
_eE = _copy.deepcopy(_eA)
_rE = _eval(_eE, set(STAFF), period="2025-01",
            lib=_lib_ctx2({"L1": {"buyer": "某某公司", "total_amount": 1000.0}},
                          {"L1": {"周立生": 1000.0}}))[0]
check("批2：库侧已收不符也不比不报（已收认定退出导入前比对，批 0 §6）",
      _rE["conf"] == "high" and not any("台账⇄库" in x for x in _rE["reasons"]),
      f"reasons={_rE['reasons']}")

# 12-6 sheet3 行不参与比对（同号 sheet2/sheet3 并存矛盾是已知噪声源，批 0 §9.6）
_eF = {"period": "2025-01",
       "invoices": [mk_inv("L3", 1000.0, sheet="sheet3",
                           remark={"receipts": [], "remaining": None,
                                   "pure_date": "2025-01-20"})],
       "prepayments": [], "problems": [],
       "sheet_totals": {"sheet3": 1000.0}, "sheet12_total": 0.0}
_rF = _eval(_eF, set(STAFF), period="2025-01", lib=_lib_ctx2())[0]
check("批2：sheet3 行不比对（库中缺失也不报，只走应收账款兜底）",
      _rF["conf"] == "low" and not any("台账⇄库" in x for x in _rF["reasons"])
      and any("应收账款纯日期" in x for x in _rF["reasons"]),
      f"reasons={_rF['reasons']}")

# 12-7 「确认」可消除台账⇄库待办（3-1 留痕是唯一消除机制）
_eG = _copy.deepcopy(_eA)
_rG = _eval(_eG, set(STAFF), confirmed={0}, period="2025-01",
            lib=_lib_ctx2({"L1": {"buyer": "某某公司", "total_amount": 800.0}}))[0]
check("批2：confirmed 命中 → 台账⇄库原因被压制（确认即消待办）",
      _rG["conf"] == "high" and not any("台账⇄库" in x for x in _rG["reasons"]),
      f"reasons={_rG['reasons']}")

# 12-8 has_split **不**压制（用户填收款 ≠ 认可台账与库一致 —— 与兜底类的关键差异）
_eH = _copy.deepcopy(_eA)
_eH["invoices"][0]["split_receipts"] = [("周立生", 500.0, "2025-01")]
_rH = _eval(_eH, set(STAFF), period="2025-01",
            lib=_lib_ctx2({"L1": {"buyer": "某某公司", "total_amount": 800.0}}))[0]
check("批2：填了逐人收款仍报台账⇄库（不吃 has_split 豁免）",
      _rH["conf"] == "low" and any("台账⇄库" in x for x in _rH["reasons"]),
      f"reasons={_rH['reasons']}")

# 12-9 维度限定到金额：库侧分摊写法与源不同（含「纯人名均分沿用首月拆分」那类豁免
# 场景）→ 只要金额一致就零扰动（原「纯人名豁免」用例已失去意义：分摊根本不比了）
_inv9 = _inv2("L9")
_inv9["handler_text"] = "周立生、陈娟"
_inv9["handlers"] = [("周立生", 500.0), ("陈娟", 500.0)]
_eI = {"period": "2025-01", "invoices": [_inv9], "prepayments": [], "problems": [],
       "sheet_totals": {"sheet1": 1000.0}, "sheet12_total": 1000.0}
_rI = _eval(_eI, set(STAFF), period="2025-01",
            lib=_lib_ctx2({"L9": {"buyer": "某某公司", "total_amount": 1000.0}},
                          {"L9": {"周立生": 800.0, "陈娟": 200.0}},
                          {"L9": [("2025-01", 1000.0, "周立生")]}))[0]
check("批2：金额一致而分摊/已收写法不同 → 零扰动（金额是唯一维度）",
      _rI["conf"] == "high" and not any("台账⇄库" in x for x in _rI["reasons"]),
      f"reasons={_rI['reasons']}")
_inv9b = _inv2("L9")
_inv9b["handler_text"] = "周立生500 陈娟500"
_inv9b["handlers"] = [("周立生", 500.0), ("陈娟", 500.0)]
_eI["invoices"] = [_inv9b]
_rIb = _eval(_eI, set(STAFF), period="2025-01",
             lib=_lib_ctx2({"L9": {"buyer": "某某公司", "total_amount": 900.0}},
                           {"L9": {"周立生": 800.0, "陈娟": 200.0}},
                           {"L9": [("2025-01", 1000.0, "周立生")]}))[0]
check("批2：对照 —— 金额不同则照报（唯一维度，销项 ⇄ 台账交叉校验）",
      _rIb["conf"] == "low"
      and any("台账⇄库不一致（金额）" in x for x in _rIb["reasons"]),
      f"reasons={_rIb['reasons']}")

# 12-10 对话框层（临时库走真 build_lib_context）：一致不扰 / 不符判待确认 / post 不比对
_rc.build_lib_context = _rc_build_lib_context_real   # 还原真函数（顶部打过桩）
_tmp2 = _P(tempfile.mkdtemp(prefix="lawfirm_smoke_p2_")) / "t.db"
try:
    _db.DB_PATH = _tmp2
    _db.init_db()
    _c2 = _db.get_conn()
    _c2.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                "VALUES (?,?,?,?,?)", ("INV-OK", "2025-01-15", "某某公司", 1000.0, "import"))
    _c2.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                "VALUES (?,?,?,?,?)", ("INV-BAD", "2025-01-15", "某某公司", 2000.0, "import"))
    _c2.execute("INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source) "
                "VALUES (?,?,?,?)", ("INV-OK", "周立生", 1000.0, "import"))
    _c2.execute("INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source) "
                "VALUES (?,?,?,?)", ("INV-BAD", "周立生", 2000.0, "import"))
    _c2.execute("INSERT INTO collection (invoice_no, amount, receipt_date, person_name, source) "
                "VALUES (?,?,?,?,?)", ("INV-OK", 1000.0, "2025-01-20", "周立生", "import"))
    _c2.commit()
    _c2.close()

    _d12 = {"period": "2025-01",
            "invoices": [mk_inv("INV-OK", 1000.0, remark=_FULL_R),
                         mk_inv("INV-BAD", 1000.0, remark=_FULL_R)],
            "prepayments": [], "problems": [],
            "sheet_totals": {"sheet1": 2000.0}, "sheet12_total": 2000.0}
    dlg12 = UnifiedImportDialog(_d12, "2025-01", STAFF)
    dlg12.show()
    check("批2：导入前模式真实建库上下文（含库侧两票）",
          dlg12._lib_ctx is not None
          and set(dlg12._lib_ctx["inv"]) == {"INV-OK", "INV-BAD"},
          str(sorted((dlg12._lib_ctx or {}).get("inv", {}))))
    _rok = next(r for r in dlg12._rows
                if r["kind"] == "invoice" and r["ev"]["invoice_no"] == "INV-OK")
    check("批2：库侧完全一致 → 该行保持高置信（真实库零扰动）",
          _rok["ev"]["conf"] == "high"
          and not any("台账⇄库" in x for x in _rok["ev"]["reasons"]),
          f"reasons={_rok['ev']['reasons']}")
    _rbad = next(r for r in dlg12._rows
                 if r["kind"] == "invoice" and r["ev"]["invoice_no"] == "INV-BAD")
    check("批2：库侧金额不符 → 该行判待确认（对话框层贯通）",
          _rbad["status"] == "待确认"
          and any("台账⇄库不一致（金额）" in x for x in _rbad["ev"]["reasons"]),
          f"status={_rbad['status']} reasons={_rbad['ev']['reasons']}")

    _d12p = {"period": "2025-01",
             "invoices": [mk_inv("INV-OK", 1000.0, remark=_FULL_R),
                          mk_inv("INV-BAD", 2000.0, remark=_FULL_R)],
             "prepayments": [], "problems": [],
             "sheet_totals": {"sheet1": 3000.0}, "sheet12_total": 3000.0}
    dlg12p = UnifiedImportDialog(_d12p, "2025-01", STAFF, mode="post")
    dlg12p.show()
    check("批2：post 模式不建库上下文、不重报差异（人工修正痕迹≠假待办）",
          dlg12p._lib_ctx is None
          and all(r["ev"]["conf"] == "high"
                  for r in dlg12p._rows if r["kind"] == "invoice"),
          str([(r["ev"]["invoice_no"], r["ev"]["conf"]) for r in dlg12p._rows
               if r["kind"] == "invoice"]))
finally:
    _db.DB_PATH = _real_db


# ------------------------------------------- 13) 批 3-3：post 编辑回写 + 还原原件
# 引擎层（apply_edit / restore_from_archive 单元）已由 tests/test_review_writeback.py
# 覆盖；本节验 **UI 接线**：重建行带镜表锚点 → post 按钮可见/可用性随行切换 →
# WritebackDialog 落库 → load_period 重载 → 还原（桩 parse_ledger_file，无需真 Excel）。
import app.engine.raw_ledger as _rl13  # noqa: E402
import app.importer.importer as _imp13  # noqa: E402
import app.ui.writeback_dialog as _wd  # noqa: E402

_wd.QMessageBox = _MB        # 回写对话框走异常分支时不许真弹窗
_orig_parse13 = _imp13.parse_ledger_file
_tmp13 = _P(tempfile.mkdtemp(prefix="lawfirm_smoke_p33_")) / "t.db"
try:
    _db.DB_PATH = _tmp13
    _db.init_db()
    _c13 = _db.get_conn()
    _c13.execute(
        "INSERT INTO import_batch (id, batch_type, period, file_name, status, "
        "imported_at, archive_path) VALUES (1,'ledger','2025-01','w.xlsx','active',"
        "datetime('now','localtime'),'X:/fake_archive.xlsx')")
    _c13.execute(
        "INSERT INTO raw_ledger (id, sheet_key, sheet_name, row_no, invoice_no, buyer,"
        " amount_raw, amount_num, handler_text, remark, kind, synced, import_batch_id)"
        " VALUES (1,'sheet1','已开票已入账',2,'INV-W','某某公司','1000',1000.0,"
        "'周立生1000','25.11.30','invoice',1,1)")
    _c13.execute(
        "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source,"
        " import_batch_id) VALUES ('INV-W','2025-01-05','某某公司',1000.0,'import',1)")
    _c13.execute(
        "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source,"
        " import_batch_id) VALUES ('INV-W','周立生',1000.0,'import',1)")
    _c13.execute(
        "INSERT INTO collection (invoice_no, amount, receipt_date, person_name, source,"
        " import_batch_id) VALUES ('INV-W',1000.0,'2025-11-30','','import',1)")
    _c13.commit()
    _c13.close()

    dlg13 = UnifiedImportDialog(mode="post")
    dlg13.load_period("2025-01")
    dlg13.show()
    _inv13 = dlg13._data["invoices"][0]
    check("批3-3：重建行带镜表锚点（raw_id=1 / synced=True）",
          _inv13.get("raw_id") == 1 and _inv13.get("synced") is True,
          f"raw_id={_inv13.get('raw_id')} synced={_inv13.get('synced')}")
    check("批3-3：post 显示编辑/还原按钮（初始禁用）",
          dlg13.btn_writeback.isVisible() and dlg13.btn_restore.isVisible()
          and not dlg13.btn_writeback.isEnabled() and not dlg13.btn_restore.isEnabled(),
          f"vis={dlg13.btn_writeback.isVisible()}/{dlg13.btn_restore.isVisible()} "
          f"en={dlg13.btn_writeback.isEnabled()}/{dlg13.btn_restore.isEnabled()}")
    check("批3-3：pre 模式不显示编辑/还原按钮（批 1b 契约不破）",
          not dlg12.btn_writeback.isVisible() and not dlg12.btn_restore.isVisible(),
          f"vis={dlg12.btn_writeback.isVisible()}/{dlg12.btn_restore.isVisible()}")

    _show_all(dlg13)
    dlg13.table.setCurrentCell(0, 0)
    check("批3-3：选中行后编辑可用、还原禁用（未修订 synced=1）",
          dlg13.btn_writeback.isEnabled() and not dlg13.btn_restore.isEnabled(),
          f"en={dlg13.btn_writeback.isEnabled()}/{dlg13.btn_restore.isEnabled()}")

    # 行内按钮可用性由 `_load_right → _set_writeback_buttons` 接线；对话框本体不 exec
    # （offscreen 阻塞）→ 直接构造 + `_on_ok`，走与真实点击完全相同的落库路径。
    raw13 = _rl13.get_row(1)
    wd13 = U.WritebackDialog(dlg13, "2025-01", "INV-W", raw13, 1)
    wd13.edits["amount_raw"].setText("1200")
    wd13.edits["handler_text"].setText("周立生1200")
    wd13.note_edit.setText("金额笔误修正")
    wd13._on_ok()
    check("批3-3：回写落库（镜表 amount_num=1200 / synced=0）",
          _db.get_conn().execute("SELECT amount_num, synced FROM raw_ledger WHERE id=1"
                                 ).fetchone()["amount_num"] == 1200
          and _db.get_conn().execute("SELECT synced FROM raw_ledger WHERE id=1"
                                     ).fetchone()["synced"] == 0,
          str(dict(_db.get_conn().execute(
              "SELECT amount_num, synced FROM raw_ledger WHERE id=1").fetchone())))
    check("批3-3：invoice.total_amount 同步 1200",
          abs(_db.get_conn().execute(
              "SELECT total_amount FROM invoice WHERE invoice_no='INV-W'"
          ).fetchone()["total_amount"] - 1200.0) < 1e-9)
    check("批3-3：charge_detail 重算 1200（经办人文本变更触发）",
          abs(_db.get_conn().execute(
              "SELECT billing_amount FROM charge_detail WHERE invoice_no='INV-W'"
          ).fetchone()["billing_amount"] - 1200.0) < 1e-9)

    dlg13._reload_post()
    _show_all(dlg13)
    dlg13.table.setCurrentCell(0, 0)
    check("批3-3：重载后行显示回写值、还原按钮解禁（synced=0）",
          dlg13._rows and "1,200.00" in dlg13._row_field(dlg13._rows[0], "amt")
          and dlg13.btn_restore.isEnabled(),
          f"amt={dlg13._row_field(dlg13._rows[0], 'amt') if dlg13._rows else '—'} "
          f"restore_en={dlg13.btn_restore.isEnabled()}")

    # 按钮路径（exec 桩）：btn_writeback.click → _writeback_row → WritebackDialog
    # （exec 桩内走与真实确定相同的 _on_ok）→ Accepted → **_reload_post 自动重载**。
    # exec 在 offscreen 会阻塞，桩只把「模态循环」换掉、落库路径逐字相同。
    from app.engine.review_writeback import apply_edit as _ae13
    _ae13("2025-01", 1, {"amount_raw": "1300", "handler_text": "周立生1300"},
          "按钮链路预置 1300")          # 引擎直改库，不重载 → 对话框行还停在 1200
    _real_wd13 = U.WritebackDialog
    _old_rows13 = dlg13._rows

    class _WD13(_real_wd13):
        def exec(self):
            self.note_edit.setText("按钮链路回写")   # 修改原因必填 → 不填会走校验提前 return
            self._on_ok()               # 字段=打开时镜表现值(1300) 原样回写
            return 1                    # QDialog.DialogCode.Accepted

    U.WritebackDialog = _WD13
    try:
        dlg13.btn_writeback.click()
    finally:
        U.WritebackDialog = _real_wd13
    check("批3-3：按钮路径回写后自动重载（行对象已换、显示 1,300.00）",
          dlg13._rows is not _old_rows13
          and "1,300.00" in dlg13._row_field(dlg13._rows[0], "amt"),
          f"rows_swapped={dlg13._rows is not _old_rows13} "
          f"amt={dlg13._row_field(dlg13._rows[0], 'amt') if dlg13._rows else '—'}")

    # 还原：parse_ledger_file 桩（还原引擎对存档重解析；无需真 Excel）。
    def _fake_parse13(path, period):
        return {"invoices": [{
            "sheet_name": "已开票已入账", "row_no": 2, "invoice_no": "INV-W",
            "buyer": "某某公司", "total_amount": 1000.0, "handler_text": "周立生1000",
            "remark_raw": "25.11.30", "case_no": "",
            "raw_row": ["1", "2025-01-05", "1000", "INV-W", "某某公司", "周立生1000",
                        "25.11.30"],
            "header": ["序号", "开票日期", "金额", "发票号码", "对方", "经办人", "备注"],
        }], "prepayments": [], "problems": []}

    _imp13.parse_ledger_file = _fake_parse13
    dlg13._restore_row()   # question 桩恒 Yes → restore_from_archive → 重载
    _row13 = _db.get_conn().execute(
        "SELECT amount_num, synced FROM raw_ledger WHERE id=1").fetchone()
    check("批3-3：还原后金额回 1000、synced=1",
          _row13["amount_num"] == 1000.0 and _row13["synced"] == 1,
          str(dict(_row13)))
    check("批3-3：还原后收款按原备注重写（1 条 1000）",
          abs(_db.get_conn().execute(
              "SELECT amount FROM collection WHERE invoice_no='INV-W'"
          ).fetchone()["amount"] - 1000.0) < 1e-9)
    _imp13.parse_ledger_file = _orig_parse13
    _show_all(dlg13)
    dlg13.table.setCurrentCell(0, 0)
    check("批3-3：还原后行恢复原值、还原按钮回到禁用（synced=1）",
          dlg13._rows and "1,000.00" in dlg13._row_field(dlg13._rows[0], "amt")
          and not dlg13.btn_restore.isEnabled() and dlg13.btn_writeback.isEnabled(),
          f"amt={dlg13._row_field(dlg13._rows[0], 'amt') if dlg13._rows else '—'} "
          f"en={dlg13.btn_writeback.isEnabled()}/{dlg13.btn_restore.isEnabled()}")
finally:
    _db.DB_PATH = _real_db


# ------------------------------------------- 汇总
bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
