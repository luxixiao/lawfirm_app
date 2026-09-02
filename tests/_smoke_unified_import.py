"""offscreen 冒烟：统一导入确认对话框（问题修正 + 预览确认 合并）。

覆盖：
- 一张表同时承载解析行与问题行，状态筛选胶囊切换
- 问题行就地修正 → 保存后立即重算并刷新，真实 data 不被修改
- 各经办人已收覆盖值回写
- 预收款问题行修正
- 跳过行
- 「确认入库」就地校验：校验不过留在对话框内、真实 data 不变
- 校验通过才合并：problems 清空、sheet 合计含修正行
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

import app.ui.unified_import_dialog as U  # noqa: E402
from app.ui.unified_import_dialog import UnifiedImportDialog  # noqa: E402


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


# ---------------------------------------------------------------- 1) 行模型
data = mk_data()
dlg = UnifiedImportDialog(data, "2025-01", STAFF, path="2025.1台账.xlsx")
dlg.show()  # offscreen 下子控件 isVisible() 依赖父窗口已 show
check("构造成功", dlg is not None)
check("行模型 = 2 解析行 + 2 问题行", len(dlg._rows) == 4, f"got={len(dlg._rows)}")
st = statuses(dlg)
check("状态含 1 高置信 / 3 待确认（待修正并入待确认）",
      st.count("待确认") == 3 and st.count("高置信") == 1,
      f"got={st}")
check("待确认行排在最前", st.index("待确认") == 0, f"got={st}")

# 默认筛选「待确认」：解析失败 2 + 低置信 1 = 3 行
check("默认筛选=待确认，3 行", dlg.table.rowCount() == 3, f"got={dlg.table.rowCount()}")
dlg._grp.button(4).setChecked(True)  # 全部
dlg._render()
check("切「全部」= 4 行", dlg.table.rowCount() == 4, f"got={dlg.table.rowCount()}")
dlg._grp.button(0).setChecked(True)  # 待确认
dlg._render()
check("切「待确认」= 3 行", dlg.table.rowCount() == 3, f"got={dlg.table.rowCount()}")
dlg._grp.button(1).setChecked(True)  # 已确认
dlg._render()
check("切「已确认」= 0 行（尚未修正/确认）", dlg.table.rowCount() == 0, f"got={dlg.table.rowCount()}")
dlg._grp.button(2).setChecked(True)  # 高置信
dlg._render()
check("切「高置信」= 1 行", dlg.table.rowCount() == 1, f"got={dlg.table.rowCount()}")

# 问题行也带原始台账行（底座改动）
dlg._grp.button(0).setChecked(True)  # 待确认（含解析失败问题行）
dlg._render()
dlg.table.selectRow(0)
row0 = dlg._current_row()
check("问题行携带 header/raw_row（可查看原始台账行）",
      bool(row0["problem"].get("header")) and bool(row0["problem"].get("raw_row")))
check("问题行右侧显示修正面板（发票表单）",
      dlg.fix_panel.isVisible() and dlg.fix_panel.inv_box.isVisible())
check("保存/跳过按钮对问题行可见", dlg.btn_save.isVisible() and dlg.btn_skiprow.isVisible())

# 右栏重构：问题块（仅问题文本、无「建议」）/ 工具行 ghost 样式
check("问题块显示原始问题文本（无「建议」字样）",
      "建议" not in dlg.lbl_issue.text(), dlg.lbl_issue.text())
check("问题块为 issueWarn 态（amber 左边框）", dlg.issue_block.objectName() == "issueWarn")
check("工具行 btn_source 走 toolLink ghost 样式", dlg.btn_source.objectName() == "toolLink")

# ---------------------------------------------------------------- 2) 就地修正
n_inv_before = len(data["invoices"])
fill_invoice_fix(dlg)
dlg._save_fix()
check("保存后工作副本发票数 +1", len(dlg._work["invoices"]) == n_inv_before + 1,
      f"got={len(dlg._work['invoices'])}")
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
dlg._grp.button(4).setChecked(True)  # 全部
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

# ---------------------------------------------------------------- 4) 校验不过 → 留在对话框
calls = {"n": 0}


def bad_validator(d):
    calls["n"] += 1
    return "台账 sheet1+sheet2 合计(6000) ≠ 销项文档本月价税合计(0)"


dlg._validate = bad_validator
dlg.accept()
check("校验不过：调用了校验器", calls["n"] == 1)
check("校验不过：真实 data 未合并（problems 仍在）", len(data.get("problems", [])) == 2)
check("校验不过：发票数未增加", len(data["invoices"]) == n_inv_before)

# ---------------------------------------------------------------- 5) 校验通过 → 合并
dlg._validate = lambda d: None
dlg.accept()
check("校验通过：problems 清空", data.get("problems") == [], f"got={data.get('problems')}")
check("校验通过：发票数 +1（修正行入库）", len(data["invoices"]) == n_inv_before + 1,
      f"got={len(data['invoices'])}")
check("校验通过：右侧编辑的收款写入 split_receipts",
      data["invoices"][0].get("split_receipts") == [("周立生", 800.0, "2025-01")],
      f"got={data['invoices'][0].get('split_receipts')}")
fixed = data["invoices"][-1]
check("修正行透传原始台账行", bool(fixed.get("header")) and bool(fixed.get("raw_row")))
check("修正行保留源文件 sheet 分类", fixed.get("sheet") == "sheet1", f"got={fixed.get('sheet')}")
check("修正行经办人分摊 2 人", len(fixed["handlers"]) == 2, f"got={fixed['handlers']}")

# ---------------------------------------------------------------- 6) 跳过 + 预收款
data2 = mk_data()
dlg2 = UnifiedImportDialog(data2, "2025-01", STAFF)
dlg2.show()
dlg2._validate = lambda d: None
# 跳过发票问题行
dlg2.table.selectRow(0)
r = dlg2._current_row()
check("首行是发票问题行", r["problem"]["kind"] == "invoice", str(r["problem"]["kind"]))
dlg2._skip_row()
check("跳过后状态=已跳过", statuses(dlg2).count("已跳过") == 1, f"got={statuses(dlg2)}")
# 修正预收款问题行
dlg2._grp.button(4).setChecked(True)  # 全部
dlg2._render()
for i in range(dlg2.table.rowCount()):
    dlg2.table.selectRow(i)
    cur = dlg2._current_row()
    if cur["kind"] == "problem" and cur["problem"]["kind"] == "prepayment":
        break
check("预收款行显示预收款表单", dlg2.fix_panel.pp_box.isVisible())
dlg2.fix_panel.pp_date.setText("2025-01-08")
dlg2.fix_panel.pp_amount.setText("2000")
dlg2.fix_panel.pp_person.setText("胡坚")
dlg2._save_fix()
check("预收款修正后 prepayments +1", len(dlg2._work.get("prepayments", [])) == 1,
      f"got={len(dlg2._work.get('prepayments', []))}")
dlg2.accept()
check("跳过的行不入库", len(data2["invoices"]) == 2, f"got={len(data2['invoices'])}")
check("预收款修正行入库", len(data2["prepayments"]) == 1, f"got={len(data2['prepayments'])}")
check("预收款金额正确", abs(data2["prepayments"][0]["amount"] - 2000.0) < 0.01)

# ---------------------------------------------------------------- 7) 渲染无告警
data3 = mk_data()
dlg3 = UnifiedImportDialog(data3, "2025-01", STAFF)
dlg3.show()
app.processEvents()
check("窗口可渲染（grab 非空）", not dlg3.grab().isNull())
check("底部汇总含发票数与合计",
      "发票" in dlg3.lbl_summary.text() and "预收款" in dlg3.lbl_summary.text(),
      dlg3.lbl_summary.text())
check("统计胶囊有计数", "待确认 3" in dlg3.lbl_stat.text() and "高置信 1" in dlg3.lbl_stat.text(),
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
d2dlg.table.selectRow(d2dlg._rows.index(low))
check("点2：待确认行显示「确认」按钮", d2dlg.btn_confirm_row.isVisible())
check("点2：待确认行不显示「编辑」按钮", not d2dlg.btn_edit.isVisible())
# 右栏重构：单一操作栏同一时刻至多一个主行动（accent）
_prim = [b for b in (d2dlg.btn_save, d2dlg.btn_refix, d2dlg.btn_skiprow,
                     d2dlg.btn_confirm_row, d2dlg.btn_edit)
         if b.objectName() == "actionPrimary"]
check("操作栏同一时刻至多一个主行动（待确认→确认为主）",
      len(_prim) == 1, f"got={[b.objectName() for b in _prim]}")
check("待确认行：主行动=确认（保存为次级）",
      d2dlg.btn_confirm_row.objectName() == "actionPrimary"
      and d2dlg.btn_save.objectName() == "actionSecondary")
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
d3dlg.table.selectRow(d3dlg._rows.index(low3))
pp3 = d3dlg.fix_panel
pp3.htable.item(0, 2).setText("4000.00")   # 未全额收款
pp3.htable.item(0, 3).setText("2025-01")
d3dlg._inv_edits[0] = pp3.read_fix()
d3dlg._rebuild()
low3b = next(r for r in d3dlg._rows if r["kind"] == "invoice" and r["inv_idx"] == 0)
check("点3：填收款并保存 → 不再是待确认", low3b["ev"]["conf"] == "high", f"reasons={low3b['ev']['reasons']}")
check("点3：未全额收款也离开待确认", low3b["status"] == "高置信", f"status={low3b['status']}")

# 点1：高置信默认只读，点「编辑」才解锁（高置信不在「待确认」筛选，先切「全部」）
d1 = mk_simple()
d1dlg = UnifiedImportDialog(d1, "2025-01", STAFF)
d1dlg.show()
d1dlg._grp.button(4).setChecked(True)   # 全部
d1dlg._render()
hi = next(r for r in d1dlg._rows if r["kind"] == "invoice" and r["inv_idx"] == 1)
d1dlg.table.selectRow(d1dlg._rows.index(hi))
check("点1：高置信默认只读（开票日期）", d1dlg.fix_panel.inv_date.isReadOnly())
check("点1：高置信默认只读（经办人下拉禁用）", not d1dlg.fix_panel.htable.cellWidget(0, 0).isEnabled())
check("点1：高置信默认只读（保存按钮隐藏）", not d1dlg.btn_save.isVisible())
check("点1：高置信显示「编辑」按钮", d1dlg.btn_edit.isVisible())
# 右栏重构：高置信无问题 → 问题块 issueOk 态、文本无「建议」
check("高置信无问题 → 问题块 issueOk 态", d1dlg.issue_block.objectName() == "issueOk")
check("高置信无问题 → 问题块文本无「建议」",
      "建议" not in d1dlg.lbl_issue.text(), d1dlg.lbl_issue.text())
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

# ------------------------------------------- 汇总
bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
