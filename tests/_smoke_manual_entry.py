"""offscreen 冒烟：补录原票页（ManualEntryView）—— 双源待补录 + 预填。

运行：python tests/_smoke_manual_entry.py
覆盖：
- 待补录表新增「来源」列（9 列），已补录页维持 8 列
- 双源行渲染（应收账款 预填完整；红字引用 只有购方/金额）
- 点「补录」→ 预填内容直接来自列表（不再现算），来源正确
- 校验失败（不在花名册）在 UI 上以「保存失败」提示，不崩溃

get_conn（backfill_module / raw_ledger / manual_entry_view）注入内存库。
"""
import os
import sqlite3
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from app.db import SCHEMA  # noqa: E402
from app.engine import backfill_module as bm  # noqa: E402
from app.engine import raw_ledger as rl  # noqa: E402
import app.ui.manual_entry_view as MV  # noqa: E402

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}", flush=True)


class _ConnProxy:
    def __init__(self, c):
        self._c = c

    def execute(self, *a, **k):
        return self._c.execute(*a, **k)

    def executescript(self, *a, **k):
        return self._c.executescript(*a, **k)

    def commit(self):
        self._c.commit()

    def close(self):
        pass

    def __getattr__(self, n):
        return getattr(self._c, n)


conn = sqlite3.connect(":memory:")
conn.row_factory = sqlite3.Row
conn.executescript(SCHEMA)
for stmt in (
    "ALTER TABLE charge_detail ADD COLUMN person_type TEXT DEFAULT ''",
    "ALTER TABLE charge_detail ADD COLUMN received_override REAL DEFAULT NULL",
    "ALTER TABLE charge_detail ADD COLUMN src_sheet TEXT DEFAULT ''",
    "ALTER TABLE charge_detail ADD COLUMN src_row INTEGER DEFAULT 0",
    "ALTER TABLE collection ADD COLUMN src_sheet TEXT DEFAULT ''",
    "ALTER TABLE collection ADD COLUMN src_row INTEGER DEFAULT 0",
    "ALTER TABLE invoice ADD COLUMN src_sheet TEXT DEFAULT ''",
    "ALTER TABLE invoice ADD COLUMN src_row INTEGER DEFAULT 0",
    "ALTER TABLE staff ADD COLUMN hire_month TEXT DEFAULT ''",
):
    conn.execute(stmt)
proxy = _ConnProxy(conn)
for mod in (bm, rl, MV):
    mod.get_conn = lambda: proxy

conn.execute("INSERT INTO staff (name, staff_type) VALUES ('周立生', '聘用')")
conn.execute(
    "INSERT INTO import_batch (id, batch_type, period, file_name, status, imported_at) "
    "VALUES (1, 'ledger', '2025-06', '2025.6台账.xlsx', 'active', datetime('now','localtime'))")
# 源 B：应收账款期外票（可全量预填）
conn.execute(
    "INSERT INTO raw_ledger (id, sheet_key, sheet_name, row_no, seq, invoice_date_raw, invoice_no, "
    "buyer, amount_raw, amount_num, handler_text, remark, case_no, kind, synced, import_batch_id) "
    "VALUES (11, 'sheet3', '应收账款', 4, '1', '24.5.6', 'D100', '乙公司', '500', 500.0, "
    "'周立生500', '25.7.10', '', 'invoice', 1, 1)")
# 源 A：红字发票引用缺失原票
conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source, orig_invoice_no) "
             "VALUES ('RED1', '2025-06-20', '丙公司', -300.0, 'import', 'MISS1')")
conn.execute("INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source) "
             "VALUES ('RED1', '周立生', -300.0, 'import')")
conn.commit()

# ---- 结构 ----
v = MV.ManualEntryView()
check("待补录表 9 列（新增「来源」）", v.tab_pending.columnCount() == 9,
      f"got={v.tab_pending.columnCount()}")
check("首列为「来源」", v.tab_pending.horizontalHeaderItem(0).text() == "来源")
check("末列为「操作」", v.tab_pending.horizontalHeaderItem(8).text() == "操作")
check("已补录表仍 8 列", v.tab_done.columnCount() == 8,
      f"got={v.tab_done.columnCount()}")

# ---- 双源渲染 ----
check("待补录 2 行", v.tab_pending.rowCount() == 2, f"got={v.tab_pending.rowCount()}")
rows = sorted(v._pending_meta.values(), key=lambda r: r["source"])
srcs = [r["source"] for r in rows]
check("来源含 应收账款 / 红字引用",
      srcs == ["红字引用", "应收账款"] or srcs == ["应收账款", "红字引用"], str(srcs))

by_src = {r["source"]: r for r in rows}
d = by_src["应收账款"]
a = by_src["红字引用"]
check("源 B 预填开票日期/对方/金额",
      d["invoice_date"] == "2024-05-06" and d["buyer"] == "乙公司" and d["total_amount"] == 500.0,
      str({k: d[k] for k in ("invoice_date", "buyer", "total_amount")}))
check("源 B 预填经办人收款",
      d["handlers"] and d["handlers"][0]["received"] == 500.0 and d["handlers"][0]["date"] == "2025-07",
      str(d["handlers"]))
check("源 A 只有购方/金额（日期留空）",
      a["invoice_date"] == "" and a["buyer"] == "丙公司" and a["total_amount"] == 300.0,
      str({k: a[k] for k in ("invoice_date", "buyer", "total_amount")}))

# 表格里能读到来源与金额文本
tbl = v.tab_pending
cells = {(tbl.item(r, 0).text(), tbl.item(r, 2).text()) for r in range(tbl.rowCount())}
check("表格显示来源列文本",
      ("应收账款", "D100") in cells and ("红字引用", "MISS1") in cells, str(cells))

# ---- 补录按钮 → 预填 ----
captured = {}
v._open_dialog = lambda prefill, edit, locked_no: captured.update(
    prefill=prefill, edit=edit, locked_no=locked_no)


def _btn_for(no):
    for r in range(tbl.rowCount()):
        if tbl.item(r, 2).text() == no:
            return tbl.cellWidget(r, 8)
    return None


check("操作列有按钮", _btn_for("D100") is not None and _btn_for("MISS1") is not None)
_btn_for("D100").click()
check("源 B 点击补录 → 预填票号/日期/金额",
      captured["prefill"]["invoice_no"] == "D100"
      and captured["prefill"]["invoice_date"] == "2024-05-06"
      and captured["prefill"]["total_amount"] == 500.0, str(captured.get("prefill")))
check("源 B 点击补录 → 预填经办人明细",
      [(h["name"], h["billing"]) for h in captured["prefill"]["handlers"]] == [("周立生", 500.0)])
check("非编辑态（可改票号）",
      captured["edit"] is False and captured["locked_no"] is False)

captured.clear()
_btn_for("MISS1").click()
check("源 A 点击补录 → 预填购方/金额、日期留空",
      captured["prefill"]["invoice_no"] == "MISS1"
      and captured["prefill"]["invoice_date"] == ""
      and captured["prefill"]["total_amount"] == 300.0, str(captured.get("prefill")))
check("源 A 预填经办人（取绝对值）",
      [(h["name"], h["billing"]) for h in captured["prefill"]["handlers"]] == [("周立生", 300.0)])

# ---- 保存校验失败在 UI 上不崩溃 ----
import app.ui.manual_entry_view as _mv  # noqa: E402


class _MB:
    calls = []

    @staticmethod
    def critical(parent, title, text):
        _MB.calls.append((title, text))

    @staticmethod
    def warning(*a, **k):
        _MB.calls.append(("warning", a[2] if len(a) > 2 else ""))


_mv.QMessageBox = _MB
try:
    bm.save_backfill({"invoice_no": "X1", "invoice_date": "2024-01-01", "total_amount": 10.0,
                      "handlers": [{"name": "查无此人", "billing": 10.0}]})
    check("校验失败抛 ValueError", False)
except ValueError as e:
    check("校验失败抛 ValueError", True, str(e))

# ------------------------------------------------------------------ #
# 批次 4 追加：字体/尺寸统一 + tab 联动 + 日期写法兼容
# ------------------------------------------------------------------ #
from PySide6.QtWidgets import QDialog as _QD  # noqa: E402

import app.ui.scale as _sc  # noqa: E402
from app.ui.date_input import DateInput, normalize_flex, parse_flex_date  # noqa: E402

# ---- 行高 / 行内按钮：不被行矩形裁掉 ----
_dss = v.tab_pending.verticalHeader().defaultSectionSize()
check("表格行高随字号档位缩放（= px(34)）", _dss == _sc.px(34), f"got={_dss} want={_sc.px(34)}")
_btn = _btn_for("D100")
check("行内「补录」按钮用 rowBtn 紧凑样式", _btn.objectName() == "rowBtn", _btn.objectName())
check("行内按钮不会被行高压扁（sizeHint 高度 <= 可用高度）",
      _btn.sizeHint().height() <= _dss - 2, f"{_btn.sizeHint().height()} vs {_dss}")

# ---- 编辑所选 / 删除所选 只对「已补录发票」tab 生效 ----
v.tabs.setCurrentIndex(0)
check("待补录 tab：编辑/删除所选 不出现",
      not v.btn_edit.isVisibleTo(v) and not v.btn_del.isVisibleTo(v), "仍在显示")
check("待补录 tab：新增补录发票 仍可用", v.btn_new.isVisibleTo(v))
v.tabs.setCurrentIndex(1)
check("已补录 tab：编辑/删除所选 出现",
      v.btn_edit.isVisibleTo(v) and v.btn_del.isVisibleTo(v), "未显示")

# ---- 日期写法兼容（用户点名的 7 种 + 常见变体） ----
for text, want, unit in (
    ("25.9.1", "2025-09-01", "day"),
    ("2025.9.1", "2025-09-01", "day"),
    ("2025.9.01", "2025-09-01", "day"),
    ("2025.09.01", "2025-09-01", "day"),
    ("25.09.1", "2025-09-01", "day"),
    ("25.09.01", "2025-09-01", "day"),
    ("25.9.01", "2025-09-01", "day"),
    ("2025-09-01", "2025-09-01", "day"),
    ("2025/9/1", "2025-09-01", "day"),
    ("2025年9月1日", "2025-09-01", "day"),
    ("2025.9", "2025-09", "month"),
    ("25.9", "2025-09", "month"),
    ("2025年9月", "2025-09", "month"),
):
    got, u = parse_flex_date(text)
    check(f"日期兼容 {text} → {want}", got == want and u == unit, f"got={got}/{u}")

check("空日期 → 视为未填", parse_flex_date("") == ("", "day"))
try:
    parse_flex_date("乱码")
    check("无法识别的日期抛异常", False)
except Exception:  # noqa: BLE001
    check("无法识别的日期抛异常", True)

# ---- DateInput：月粒度截到月 / 日粒度保留日 / 非法可辨别 ----
_di = DateInput("month")
_di.set_text("25.9.1")
check("DateInput(月) 25.9.1 → 2025-09", _di.text() == "2025-09", _di.text())
_di.set_text("2025.9")
check("DateInput(月) 2025.9 → 2025-09", _di.text() == "2025-09", _di.text())
_di.set_text("乱码")
check("DateInput 非法 → 空值且 is_valid()=False",
      _di.text() == "" and not _di.is_valid() and bool(_di.error()), _di.error())
_di.set_text("")
check("DateInput 空 → 未填且视为合法", _di.is_empty() and _di.is_valid())
_di2 = DateInput("day")
_di2.set_text("25.9.1")
check("DateInput(日) 25.9.1 → 2025-09-01", _di2.text() == "2025-09-01", _di2.text())
check("normalize_flex(月) 截到月", normalize_flex("25.9.1", "month") == "2025-09")

# ---- 补录弹窗：收款日期换成柔性日期控件（可手输 25.9） ----
_opens = []
_exec_orig = _QD.exec
_QD.exec = lambda self: (_opens.append(self), 0)[1]      # 0 = Rejected（只建控件不保存）
try:
    # 注意：前面的段落把 v._open_dialog 换成了捕获用的 lambda，
    # 这里直接调类上的真方法。
    MV.ManualEntryView._open_dialog(
        v,
        {"invoice_no": "Z1", "invoice_date": "25.9.1", "buyer": "甲",
         "total_amount": 100.0,
         "handlers": [{"name": "周立生", "billing": 100.0,
                       "received": 100.0, "date": "25.9"}]},
        edit=False, locked_no=False)
finally:
    _QD.exec = _exec_orig
_dlg = _opens[-1] if _opens else None
_dts = _dlg.findChildren(DateInput) if _dlg else []
check("补录弹窗收款日期用柔性日期控件", len(_dts) == 1, f"got={len(_dts)} 个")
check("已收款行的日期预填 '25.9' 归一为 2025-09",
      bool(_dts) and _dts[0].text() == "2025-09",
      _dts[0].text() if _dts else "-")
check("明细表里 25.9 这类写法也能被 _read_detail 读出",
      bool(_dts) and _dts[0].is_valid())

# ------------------------------------------------------------------ #
# 阶段 3（B2f）：BackfillDialog 抽取 —— 复核页与补录页共用的弹窗
# （两处调用点：本页 open_backfill_pending/open_backfill → _open_dialog；
#   复核页行内「补录原票」→ 同一个类）
# ------------------------------------------------------------------ #
import app.ui.backfill_dialog as BD  # noqa: E402
from PySide6.QtWidgets import QDialogButtonBox as _QDBB  # noqa: E402


class _MB2:
    """记录 (title, text)，便于区分「提示」与「无法保存」两类。"""
    calls = []

    @staticmethod
    def warning(parent, title, text, *a, **k):
        _MB2.calls.append((title, text))

    @staticmethod
    def critical(parent, title, text, *a, **k):
        _MB2.calls.append((title, text))


BD.QMessageBox = _MB2


def _pf(no="Z2", date="2025-09-01", total=100.0, handlers=None):
    return {"invoice_no": no, "invoice_date": date, "buyer": "甲", "total_amount": total,
            "handlers": handlers if handlers is not None else [
                {"name": "周立生", "billing": 100.0, "received": 0.0, "date": ""}]}


# 1) 默认态：票号可改、明细预填、校验通过后 data() 归一化
_d1 = BD.BackfillDialog(_pf())
check("BackfillDialog：票号可改（新增态）", not _d1.no_edit.isReadOnly())
check("BackfillDialog：明细表预填 1 行", _d1.detail.rowCount() == 1,
      f"got={_d1.detail.rowCount()}")
_d1._on_accept()
check("BackfillDialog：校验通过 → data() 归一化（日期 YYYY-MM-DD）",
      _d1.data() is not None and _d1.data()["invoice_date"] == "2025-09-01",
      str(_d1.data()))
check("BackfillDialog：data() 带 handlers",
      bool(_d1.data()) and _d1.data()["handlers"][0]["name"] == "周立生",
      str(_d1.data()))

# 2) locked_no：票号只读（编辑已补录票，票号是主键）
_d2 = BD.BackfillDialog(_pf(), locked_no=True)
check("BackfillDialog locked_no：票号只读", _d2.no_edit.isReadOnly())

# 3) validator（复核页用它跑 build_backfill）失败 → 留在弹窗、data() 仍为 None
_MB2.calls.clear()
_d3 = BD.BackfillDialog(_pf(), validator=lambda d: "补录票号 Z2 已在库中，无需补录。")
_d3._on_accept()
check("BackfillDialog：validator 失败 → 不 accept", _d3.data() is None, str(_d3.data()))
check("BackfillDialog：validator 失败 → 「无法保存」提示原样透出",
      any(t == "无法保存" and "已在库中" in x for t, x in _MB2.calls), str(_MB2.calls))

# 4) 字段级校验四连：都留在弹窗（data() 恒为 None）
_MB2.calls.clear()
_d4 = BD.BackfillDialog(_pf())
_d4.no_edit.setText("")
_d4._on_accept()
check("BackfillDialog：票号空 → 提示且不放行",
      _d4.data() is None and any(t == "提示" and "发票号码不能为空" in x for t, x in _MB2.calls),
      str(_MB2.calls))

_d5 = BD.BackfillDialog(_pf(date=""))
_d5._on_accept()
check("BackfillDialog：开票日期空 → 提示且不放行",
      _d5.data() is None and any("开票日期为必填项" in x for _t, x in _MB2.calls), str(_MB2.calls))

_d6 = BD.BackfillDialog(_pf(date="乱码"))
_d6._on_accept()
check("BackfillDialog：开票日期无法识别 → 提示且不放行",
      _d6.data() is None and any("开票日期无法识别" in x for _t, x in _MB2.calls),
      str(_MB2.calls))

_d7 = BD.BackfillDialog(_pf(total=50.0, handlers=[
    {"name": "周立生", "billing": 100.0, "received": 100.0, "date": "2025-09"}]))
_d7._on_accept()
check("BackfillDialog：已收超价税合计 → 提示且不放行",
      _d7.data() is None and any("超过价税合计" in x for _t, x in _MB2.calls), str(_MB2.calls))

# 5) readonly（复核页「查看原票」）：全只读 + 仅「关闭」按钮 + 不产出数据
_d8 = BD.BackfillDialog(_pf(), title="查看原票", readonly=True)
check("BackfillDialog readonly：票号/日期/明细均不可编辑",
      not _d8.no_edit.isEnabled() and not _d8.date_edit.isEnabled()
      and not _d8.detail.isEnabled())
check("BackfillDialog readonly：增删行按钮隐藏",
      not _d8.b_add.isVisibleTo(_d8) and not _d8.b_del.isVisibleTo(_d8))
_bb = _d8.findChildren(_QDBB)
_std = _bb[0].standardButtons() if _bb else _QDBB.StandardButton.NoButton
check("BackfillDialog readonly：仅「关闭」按钮",
      _std == _QDBB.StandardButton.Close, str(_std))
check("BackfillDialog readonly：不产出 data()", _d8.data() is None)

# 6) 共用的 backfill_validator（与写库同源：build_backfill）
_err = BD.backfill_validator({"invoice_no": "V1", "invoice_date": "2024-01-01",
                              "total_amount": 10.0,
                              "handlers": [{"name": "查无此人", "billing": 10.0}]})
check("backfill_validator：不在花名册 → 返回文案",
      bool(_err) and "不在职工花名册" in _err, str(_err))
check("backfill_validator：合规 → None",
      BD.backfill_validator({"invoice_no": "V2", "invoice_date": "2024-01-01",
                             "total_amount": 10.0,
                             "handlers": [{"name": "周立生", "billing": 10.0,
                                           "received": 0.0, "date": ""}]}) is None)

bad = [n for n, ok in results if not ok]
print(f"\nSMOKE {'PASS' if not bad else 'FAIL'} {len(results) - len(bad)}/{len(results)}")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
