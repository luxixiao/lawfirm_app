"""offscreen 冒烟：阶段 5 —— 红字行「待补录」+ 新增「已补录」叠加视图

用户口径（2026-09-18 拍板）：
- Q1 = **B 乙**：红字发票本身没问题，缺的是它引用的**蓝字原票** → 让**红字行自己**
  落「待补录」，原因列写明补的是哪张原票；补完**回落到它本来的**高置信 / 待确认。
- Q2 = **A 甲**：新增「已补录」筛选（夹在待确认与已确认之间），只看**本次导入填过**的
  补录；它是**叠加视图**，行仍留在自己的状态里（高置信 / 待确认）。

覆盖：
  A 原票缺失（不在库、不在本批）      → 待补录 + 原因列带原票号 + 按钮「补录原票」
  B 原票在本批 sheet1/2              → **不**待补录、**无**补录入口（防撞写前校验③卡整批）
  C 原票在本批 sheet3(deferred)      → 红字行不待补录，deferred 行自己待补录（不重复两行）
  D 原票已在库                        → 不待补录，按钮「查看原票」
  E 反查不到原票号                    → 不待补录、按钮隐藏
  F 普通（非红字）行不受影响
  G 待补录仍排最前（_PRIO 未受影响）
  H 补录后：状态回落 + 进「已补录」+ **仍在**「高置信」（叠加）
  I 补录后按钮变「修改补录」
  J deferred 行补录后：进「已补录」+ 仍在「待确认」
  K 待补录红字行不显示「确认」；硬调 _confirm_row 也零写入
  L 筛选栏顺序 / FILTER_ALL
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

import app.ui.unified_import_dialog as U  # noqa: E402


class _MB:
    calls: list = []

    class Icon:
        Information = Warning = Critical = 0
        Question = 0

    class ButtonRole:
        AcceptRole = RejectRole = DestructiveRole = ActionRole = 0

    @staticmethod
    def information(parent, title, text, *a, **k):
        _MB.calls.append(("information", title, text))

    warning = information
    critical = information
    question = information


U.QMessageBox = _MB

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")


STAFF = ["周立生", "陈娟", "胡坚", "柳立中"]
HEADER = ["发票号码", "购方名称", "价税合计", "经办人", "备注"]
CLEAN_REMARK = {"receipts": [], "remaining": None, "pure_date": None,
                "is_red_remark": False, "is_red_off": False}


def mk_inv(no, total, sheet="sheet1", remark=None, handler="周立生", is_red=None):
    return {
        "sheet": sheet,
        "sheet_name": {"sheet1": "已开票已入账", "sheet2": "已开票未入账",
                       "sheet3": "应收账款"}.get(sheet, sheet),
        "row_no": 10,
        "header": HEADER,
        "raw_row": [no, "某某公司", str(total), handler, ""],
        "invoice_no": no, "invoice_date": "2025-01-15", "buyer": "某某公司",
        "total_amount": total, "handlers": [(handler, float(total))],
        "handler_text": handler, "remark_raw": "",
        "remark": remark or dict(CLEAN_REMARK),
        "case_no": "", "is_red": (total < 0 if is_red is None else is_red),
        "split_receipts": [],
    }


def mk_red(no, orig_total=-3000.0, **kw):
    """红字行：`is_red=True`、无收款疑问（故自身判定为高置信）。"""
    return mk_inv(no, orig_total, **kw)


def mk_data(invoices, period="2025-01"):
    return {
        "period": period,
        "invoices": list(invoices),
        "prepayments": [], "problems": [], "deferred": [],
        "sheet_totals": {"sheet1": 0.0}, "sheet12_total": 0.0,
    }


# ---------------------------------------------------------------- DB 桩
_RED: dict = {}        # 红字票号 → 它引用的原票号（_red_orig_no 直查）
_LIB_DB: set = set()   # _invoice_in_library 直查的「已在库」票号


class _Cur:
    def __init__(self, one=None):
        self._one = one

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []

    def __iter__(self):
        return iter(())

    def __getitem__(self, k):
        return self._one[k]


class _Conn:
    """极简连接桩：只为 `_red_orig_no` / `_invoice_in_library` 两条查询服务。"""

    def execute(self, sql, params=()):
        s = " ".join(str(sql).lower().split())
        p = tuple(params or ())
        if "orig_invoice_no from invoice" in s:
            red = p[0] if p else ""
            return _Cur({"orig_invoice_no": _RED[red]} if red in _RED else None)
        if s.startswith("select 1 from invoice"):
            return _Cur({"1": 1} if (p[0] if p else "") in _LIB_DB else None)
        return _Cur(None)

    def close(self):
        pass


U.get_conn = lambda: _Conn()
U.load_invoice_detail = lambda no: {
    "invoice_no": no, "invoice_date": "2024-01-01", "buyer": "库中购方",
    "total_amount": 900.0, "handlers": [{"name": "周立生", "billing": 900.0}]}
U.prefill_red_original = lambda conn, no: {
    "invoice_no": no, "invoice_date": "", "buyer": "红字参考购方",
    "total_amount": 3000.0,
    "handlers": [{"name": "周立生", "billing": 3000.0, "received": 0.0, "date": ""}]}


class _FakeBF:
    payload = None
    seen: list = []

    def __init__(self, prefill=None, *, title="", locked_no=False,
                 readonly=False, validator=None, parent=None):
        type(self).seen.append({"title": title, "readonly": readonly,
                                "prefill": dict(prefill or {})})

    def exec(self):
        return (U.QDialog.DialogCode.Accepted
                if type(self).payload is not None
                else U.QDialog.DialogCode.Rejected)

    def data(self):
        return dict(type(self).payload or {})


U.BackfillDialog = _FakeBF


# ---------------------------------------------------------------- 助手
def _show(dlg, name):
    """切到指定筛选并重渲染。"""
    dlg._grp.button(U.FILTERS.index(name)).setChecked(True)
    dlg._render()


def _show_all(dlg):
    dlg._grp.button(U.FILTER_ALL).setChecked(True)
    dlg._render()


def _select(dlg, r):
    """选行：必须 setCurrentCell（只 selectRow 不改「当前项」，_current_row 会读到旧行）。"""
    i = dlg._rows.index(r)
    dlg.table.setCurrentCell(i, 0)
    dlg.table.selectRow(i)
    dlg._load_right()


def _red_row(dlg):
    return next(r for r in dlg._rows if r["kind"] == "invoice" and r["ev"]["is_red"])


def _plain_row(dlg):
    return next(r for r in dlg._rows if r["kind"] == "invoice" and not r["ev"]["is_red"])


def _def_row(dlg):
    return next(r for r in dlg._rows if r["kind"] == "deferred")


def mk_dlg(invoices, red_map=None, lib=None, period="2025-01", show_all=True):
    _RED.clear()
    _RED.update(red_map or {})
    _LIB_DB.clear()
    _LIB_DB.update(lib or set())
    # split_deferred 决定 sheet3 行的 need_backfill，需要「库中票号集合」：
    # 这里与 _LIB_DB 同源打桩（真实运行时两者同一个库）。
    import app.importer.ledger_import as LI
    LI.library_invoice_nos = lambda conn=None: set(_LIB_DB)
    dlg = U.UnifiedImportDialog(mk_data(invoices), period, STAFF)
    dlg.show()
    # 默认筛选是「待补录」（button(0)）；只在实际断言「全部」可见时才切走，
    # 否则测「默认筛选」的用例会被这行顺手改掉。
    if show_all:
        _show_all(dlg)
    return dlg


# ================================================================ A) 原票缺失
d = mk_dlg([mk_red("RED-1"), mk_inv("INV-HI", 1000.0,
                                    remark={"receipts": [("2025-01", 0)], "remaining": None,
                                            "pure_date": None})],
           red_map={"RED-1": "ORIG-9"})
r = _red_row(d)
check("A：原票不在库/不在本批 → 红字行落「待补录」", r["status"] == "待补录", r["status"])
check("A：待补录时记下要补的蓝字原票号", r.get("bf_orig") == "ORIG-9", str(r.get("bf_orig")))
check("A：原因列写明补的是哪张原票",
      d._row_field(r, "reason") == "需补录原票（红字引用 ORIG-9）",
      d._row_field(r, "reason"))
_select(d, r)
check("A：右侧按钮 = 「补录原票」",
      d.btn_backfill.isVisible() and d.btn_backfill.text() == "补录原票",
      f"vis={d.btn_backfill.isVisible()} text={d.btn_backfill.text()}")
check("A：待补录行**不**给「确认」（点了写不出东西）",
      not d.btn_confirm_row.isVisible(), str(d.btn_confirm_row.isVisible()))

# ---- 本行自身另带疑问时，原因列两者都显示（绝不因待补录而隐藏） ----
d_self = mk_dlg([mk_red("RED-1", handler="查无此人")], red_map={"RED-1": "ORIG-9"})
r_self = _red_row(d_self)
check("A：待补录 + 本行自身疑问 → 原因列两者都写",
      d_self._row_field(r_self, "reason").startswith("需补录原票（红字引用 ORIG-9）；")
      and "花名册" in d_self._row_field(r_self, "reason"),
      d_self._row_field(r_self, "reason"))

# ================================================================ B) 原票在本批 sheet1/2
d_b = mk_dlg([mk_red("RED-1"), mk_inv("ORIG-9", 1000.0)], red_map={"RED-1": "ORIG-9"})
r_b = _red_row(d_b)
check("B：原票在本批 sheet1/2 → **不**判待补录（本次随台账入库）",
      r_b["status"] != "待补录", r_b["status"])
check("B：且不给补录入口（否则写前校验③会卡住整批）",
      d_b._row_backfill_target(r_b) is None, str(d_b._row_backfill_target(r_b)))
_select(d_b, r_b)
check("B：按钮隐藏", not d_b.btn_backfill.isVisible())

# ================================================================ C) 原票在本批 sheet3
d_c = mk_dlg([mk_red("RED-1"),
              mk_inv("ORIG-9", 2000.0, sheet="sheet3",
                     remark={"receipts": [], "remaining": None, "pure_date": "2024-11-20"})],
             red_map={"RED-1": "ORIG-9"})
r_c = _red_row(d_c)
check("C：原票在本批 sheet3 → 红字行不待补录（deferred 行自己就是待补录行）",
      r_c["status"] != "待补录", r_c["status"])
check("C：deferred 行自己落在待补录（票号 = 原票号）",
      _def_row(d_c)["status"] == "待补录"
      and (_def_row(d_c)["deferred"].get("invoice_no") == "ORIG-9"),
      f"{_def_row(d_c)['status']} / {_def_row(d_c)['deferred'].get('invoice_no')}")
check("C：待补录只有 1 行（不因红字再指一次而变 2 行）",
      sum(1 for x in d_c._rows if x["status"] == "待补录") == 1,
      str([(x["kind"], x["status"]) for x in d_c._rows]))

# ================================================================ D) 原票已在库
d_d = mk_dlg([mk_red("RED-1"), mk_inv("INV-HI", 1000.0)], red_map={"RED-1": "ORIG-9"},
             lib={"ORIG-9"})
r_d = _red_row(d_d)
check("D：原票已在库 → 不待补录", r_d["status"] != "待补录", r_d["status"])
_select(d_d, r_d)
check("D：按钮 = 「查看原票」", d_d.btn_backfill.text() == "查看原票", d_d.btn_backfill.text())

# ================================================================ E) 反查不到原票号
d_e = mk_dlg([mk_red("RED-1"), mk_inv("INV-HI", 1000.0)])
r_e = _red_row(d_e)
check("E：取不到原票号 → 不待补录", r_e["status"] != "待补录", r_e["status"])
_select(d_e, r_e)
check("E：按钮隐藏", not d_e.btn_backfill.isVisible())

# ================================================================ F) 普通行不受影响
d_f = mk_dlg([mk_red("RED-1"), mk_inv("INV-HI", 1000.0,
                                       remark={"receipts": [("2025-01", 0)], "remaining": None,
                                               "pure_date": None})],
             red_map={"RED-1": "ORIG-9"})
p_f = _plain_row(d_f)
check("F：普通发票行状态照旧（高置信）", p_f["status"] == "高置信", p_f["status"])
check("F：普通行永不成为补录目标", d_f._red_backfill_orig(p_f) == ""
      and d_f._row_backfill_target(p_f) is None)

# ================================================================ G) 待补录仍排最前
check("G：待补录排最前（_PRIO 未受影响）",
      [x["status"] for x in d_f._rows][0] == "待补录",
      str([x["status"] for x in d_f._rows]))

# ================================================================ H/I) 补录后回落 + 叠加
_FakeBF.payload = {
    "invoice_no": "ORIG-9", "invoice_date": "2024-06-01", "buyer": "红字参考购方",
    "total_amount": 3000.0,
    "handlers": [{"name": "周立生", "billing": 3000.0,
                  "received": 0.0, "date": "", "date_raw": ""}],
}
_FakeBF.seen.clear()
_select(d, r)
d._open_backfill()
check("H：补录收集的是「引用原票」号（不是红字票号）",
      "ORIG-9" in d._backfills and "RED-1" not in d._backfills, str(list(d._backfills)))
r2 = _red_row(d)
check("H：补完**回落到本来的高置信**（红字票本身没问题）",
      r2["status"] == "高置信", r2["status"])
check("H：原因列回到「✓ 系统判定无疑问」",
      d._row_field(r2, "reason") == "✓ 系统判定无疑问", d._row_field(r2, "reason"))
check("H：挂上「已补录」叠加标记", r2.get("is_backfilled") is True)
_show(d, "已补录")
check("H：「已补录」筛出 1 行", d.table.rowCount() == 1, f"got={d.table.rowCount()}")
_show(d, "高置信")
check("H：同一行**仍在「高置信」**（叠加视图，互不影响）",
      d.table.rowCount() >= 1, f"got={d.table.rowCount()}")
_show(d, "待补录")
check("H：已补录后再看「待补录」= 0 行", d.table.rowCount() == 0,
      f"got={d.table.rowCount()}")
_select(d, r2)
check("I：按钮变「修改补录」", d.btn_backfill.text() == "修改补录", d.btn_backfill.text())
check("I：补完不再有「待补录」遗留 → 底部汇总不再报需补录",
      "需补录原票" not in d.lbl_summary.text(), d.lbl_summary.text())
check("I：状态栏出现「已补录 1」", "已补录 1" in d.lbl_stat.text(), d.lbl_stat.text())

# ================================================================ J) deferred 行同理
d_j = mk_dlg([mk_inv("AR-1", 5000.0, sheet="sheet3",
                     remark={"receipts": [], "remaining": None, "pure_date": "2024-11-20"})])
r_j = _def_row(d_j)
check("J：缺号 sheet3 行落待补录（阶段 4-2 口径未变）", r_j["status"] == "待补录", r_j["status"])
_FakeBF.payload = {
    "invoice_no": "AR-1", "invoice_date": "2024-11-05", "buyer": "应收公司",
    "total_amount": 5000.0,
    "handlers": [{"name": "周立生", "billing": 5000.0,
                  "received": 0.0, "date": "", "date_raw": ""}],
}
_select(d_j, r_j)
d_j._open_backfill()
r_j2 = _def_row(d_j)
check("J：deferred 补完落「待确认」（阶段 4-2 A 甲，未变）",
      r_j2["status"] == "待确认", r_j2["status"])
_show(d_j, "已补录")
check("J：「已补录」能筛到 deferred 补录行", d_j.table.rowCount() == 1,
      f"got={d_j.table.rowCount()}")
_show(d_j, "待确认")
check("J：同一行**仍在「待确认」**", d_j.table.rowCount() == 1, f"got={d_j.table.rowCount()}")

# ================================================================ K) 确认零写入
d_k = mk_dlg([mk_red("RED-1"), mk_inv("INV-HI", 1000.0)], red_map={"RED-1": "ORIG-9"})
r_k = _red_row(d_k)
_select(d_k, r_k)
_MB.calls.clear()
d_k._confirm_row()
check("K：待补录红字行点「确认」→ 可读提示", bool(_MB.calls)
      and "原票" in _MB.calls[-1][2], str(_MB.calls[-1:]))
check("K：且**零写入**（未记已确认）", d_k._confirmed == set(), str(d_k._confirmed))
check("K：状态仍是待补录", _red_row(d_k)["status"] == "待补录", _red_row(d_k)["status"])

# ================================================================ L) 筛选栏
check("L：筛选栏顺序 = 待补录/待确认/已补录/已确认/高置信/已跳过/全部",
      U.FILTERS == ["待补录", "待确认", "已补录", "已确认", "高置信", "已跳过", "全部"],
      str(U.FILTERS))
check("L：FILTER_ALL 指向「全部」", U.FILTERS[U.FILTER_ALL] == "全部", str(U.FILTER_ALL))
d_l = mk_dlg([mk_inv("INV-1", 1000.0,
                     remark={"receipts": [("2025-01", 0)], "remaining": None, "pure_date": None})],
             show_all=False)
check("L：默认筛选仍是「待补录」", d_l._grp.checkedId() == 0, str(d_l._grp.checkedId()))

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
