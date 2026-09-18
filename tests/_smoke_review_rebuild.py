"""offscreen 冒烟：导入后读库重建（批 1b）。

覆盖三层：
A. `ledger_import.apply_sheet_receipt_rule` —— 批 1b 从 `_parse_invoice_sheet` 内联代码
   抽出的共享函数（sheet1 兜底 / sheet2 纯日期降级 / sheet3 保留 / 红字不加兜底）。
B. `app.engine.review_rebuild` —— raw_ledger(+collection) → 与解析结果同构的 data：
   · 分桶（sheet1/2→invoices、sheet3→deferred、sheet4→prepayments）、sheet_totals、
     sheet12_total、problems 恒空、backfills 恒空、批次溯源三要素；
   · **往返等价性**（本批最关键的一条）：同一份台账行先走解析器 → `_insert_raw_ledger`
     落镜表 → 再由 `rebuild_period_data` 重建 → 两层结果的业务字段**逐字段一致**
     （证明「读库重建」与「源文件解析」口径同源，不是各写一份）；
   · 显式逐人收款（split_receipts）从本批 `collection` 读回，且**只在备注推不出收款时**
     注入（否则会把「各经办人已收」打成 0）；
   · 无批次账期 → 空骨架；`read_ledger_row` 存档不可读 → `([], [])`。
C. `UnifiedImportDialog(mode="post")` —— 只读契约：
   底部隐藏「确认入库」+「取消」改「关闭」；右栏表单只读；行内写按钮与「补录原票」全隐藏；
   `accept()` 不写库；sheet3「已在库」行归高置信（导入后无待办），「待补录」仍是有效待办；
   pre 模式（默认）行为一字不变。
D. 批 3-1「导入时确认」留痕：导入那一刻点过「确认」的票（`anomaly_note` 独立 dim）→
   post 模式**不再重报成「待确认」**（否则 post 隐藏了「确认」按钮 = 用户点不掉的假待办）；
   **逐票判定**（同类的未确认行仍留「待确认」）；疑问原文照旧展示，仅追加「导入时已确认」标注；
   **pre 模式不读留痕**（确认是实时的 `_confirmed` 下标；读留痕会让「覆盖式重导同账期」时
   上一批的旧确认提前吞掉本批的疑问）。引擎/写库/读库三层的完整覆盖见 `test_import_confirm.py`。
E. 批 3-2「经办人分摊」取**库侧真值**：`raw_ledger` 只存台账原文 ⇒ 导入时人工改过的分摊
   重解析会在 post 退回旧值/空值。真值在 `charge_detail`，且**只认本台账批次**（可证明是
   本次导入写入的），并要求与行金额勾稽才采用。含 4 条反向探针：非本批次不取 / 不勾稽不取 /
   sheet3 行不取别的批次 / 无库值完整回退原文。**顺序依赖**：E 节必须在 B2 往返等价之后。

运行：python tests/_smoke_review_rebuild.py   （需 QT_QPA_PLATFORM=offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

import app.db as _db  # noqa: E402
import app.ui.unified_import_dialog as U  # noqa: E402
from app.engine import review_rebuild as RB  # noqa: E402
from app.importer.importer import _insert_raw_ledger  # noqa: E402
from app.importer.ledger_import import (  # noqa: E402
    _parse_invoice_sheet, _parse_sheet4, apply_sheet_receipt_rule, split_deferred,
)
from app.importer.parse_remark import parse_remark  # noqa: E402
from app.ui.unified_import_dialog import (  # noqa: E402
    FILTER_ALL, REASON_BACKFILL, REASON_IMPORT_CONFIRMED, REASON_NO_DOUBT,
    UnifiedImportDialog,
)

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")


class _MB:
    """offscreen 下 QMessageBox.exec() 会阻塞，用桩替换：只记录、不弹窗。"""
    calls: list = []

    @staticmethod
    def warning(parent, title, text, *a, **k):
        _MB.calls.append(("warning", text))

    @staticmethod
    def information(parent, title, text, *a, **k):
        _MB.calls.append(("information", text))

    @staticmethod
    def question(parent, title, text, *a, **k):
        _MB.calls.append(("question", text))
        return 1


U.QMessageBox = _MB
import app.ui.problem_fix_panel as _pfp  # noqa: E402

_pfp.QMessageBox = _MB

STAFF = ["周立生", "陈娟", "胡坚", "柳立中"]


# ==================================================================== A) 共享规则
def _rem(receipts=None, pure=None):
    return {"receipts": list(receipts or []), "remaining": None, "pure_date": pure,
            "is_red_remark": False, "is_red_off": False}


r = parse_remark("25.1.20", default_year=2025)
apply_sheet_receipt_rule("sheet2", r, 2000.0, "2025-01")
check("A1 sheet2 纯日期 → 降级未收（pure_date 与 receipts 双清）",
      r["pure_date"] is None and r["receipts"] == [], f"{r}")

r = _rem()
apply_sheet_receipt_rule("sheet1", r, 1000.0, "2025-01")
check("A2 sheet1 空备注 → 全额兜底 (账期, 0.0)", r["receipts"] == [("2025-01", 0.0)], f"{r}")

r = _rem(receipts=[("2025-01-10", 500.0)])
apply_sheet_receipt_rule("sheet1", r, 1000.0, "2025-01")
check("A3 sheet1 备注已有收款 → 不覆盖", r["receipts"] == [("2025-01-10", 500.0)], f"{r}")

r = _rem()
apply_sheet_receipt_rule("sheet1", r, -1000.0, "2025-01")
check("A4 sheet1 红字（total<0）→ 不加兜底", r["receipts"] == [], f"{r}")

r = parse_remark("25.2.28", default_year=2025)
apply_sheet_receipt_rule("sheet3", r, 3000.0, "2025-01")
check("A5 sheet3 纯日期 → 保留（= 该历史应收的收款日期）",
      (r["pure_date"] or "").startswith("2025-02") and r["receipts"], f"{r}")


# ==================================================================== B) 临时库
_HDR_INV = ["序号", "开票日期", "发票号码", "对方", "金额", "经办人", "备注", "案号"]
_HDR_PP = ["序号", "收到日期", "发票号码", "对方", "金额", "经办人", "备注", "案号"]

# 覆盖：sheet1 空备注兜底 / sheet2 纯日期降级 / sheet2 显式逐人收款（collection）
#       sheet3 缺号（待补录）/ sheet3 已在库 / sheet4 预收款
_ROWS1 = [
    _HDR_INV,
    ["1", "2025.1.10", "P1", "甲公司", "10,000", "周立生", "", "案1"],
    ["2", "2025.1.11", "P2", "乙公司", "-2,000", "陈娟", "", "案2"],
]
_ROWS2 = [
    _HDR_INV,
    # ⚠️ 纯日期必须是 `parse_remark` 认的写法（2 位年，如 25.1.20）；
    #    「2025.1.20」它**不认**（实测）→ 会把 B4 写成「本来就为空」的空转断言。
    ["1", "2025.1.12", "P3", "丙公司", "2,000", "陈娟", "25.1.20", ""],
    ["2", "2025.1.13", "P4", "丁公司", "4,000", "胡坚", "", ""],
]
_ROWS3 = [
    _HDR_INV,
    ["1", "2024.11.5", "P5", "戊公司", "3,000", "胡坚", "25.2.28", ""],
    ["2", "2024.12.1", "P6", "己公司", "5,000", "柳立中", "", ""],
]
_ROWS4 = [
    _HDR_PP,
    ["1", "2025.1.8", "", "庚公司", "800", "胡坚", "", ""],
]

_real_db = _db.DB_PATH
_tmpdir = Path(tempfile.mkdtemp(prefix="lawfirm_rebuild_"))
_tmpdb = _tmpdir / "t.db"
_db.DB_PATH = _tmpdb
_db.init_db()

_conn = _db.get_conn()
for _n in STAFF:
    _conn.execute("INSERT INTO staff (name, staff_type, is_active) VALUES (?,?,1)", (_n, "聘用"))
# P6 已在库（sheet3「已入库」态）；P1/P3/P4 建最小发票行（collection 有外键约束）
for _no, _dt, _amt in (("P6", "2024-12-01", 5000.0), ("P1", "2025-01-10", 10000.0),
                       ("P3", "2025-01-12", 2000.0), ("P4", "2025-01-13", 4000.0)):
    _conn.execute(
        "INSERT INTO invoice (invoice_no, invoice_date, total_amount, source) VALUES (?,?,?,?)",
        (_no, _dt, _amt, "import"))
_conn.execute(
    "INSERT INTO import_batch (batch_type, period, file_name, archive_path, imported_at, status) "
    "VALUES ('ledger','2025-01','2025.1台账.xlsx','测试/存档/2025.1台账.xlsx','2025-02-01 10:00', 'active')")
_batch_id = _conn.execute("SELECT id FROM import_batch ORDER BY id DESC LIMIT 1").fetchone()[0]

# 解析 → 逐行落镜表（走真实写入函数 `_insert_raw_ledger`，即生产同一条路径）
_parsed = {"invoices": [], "deferred": [], "prepayments": [], "sheet_totals": {}, "problems": []}
for _key, _rows in (("sheet1", _ROWS1), ("sheet2", _ROWS2), ("sheet3", _ROWS3)):
    _items, _probs = _parse_invoice_sheet(_rows, _key,
                                         f"202501{_key}", "2025-01")
    _parsed["problems"].extend(_probs)
    _parsed["invoices"].extend(_items)
    _parsed["sheet_totals"][_key] = sum(i["total_amount"] for i in _items)
_pp, _pp_probs = _parse_sheet4(_ROWS4, "2025已入账未开票", "2025-01")
_parsed["prepayments"] = _pp
_parsed["problems"].extend(_pp_probs)
_parsed["sheet12_total"] = _parsed["sheet_totals"]["sheet1"] + _parsed["sheet_totals"]["sheet2"]
split_deferred(_parsed, "2025-01", in_library={"P6"})

check("B0 解析侧：0 问题行（fixture 全部可解析）", _parsed["problems"] == [],
      f"{[p['reason'] for p in _parsed['problems']]}")
check("B0 解析侧：sheet3 全行转入 deferred", len(_parsed["deferred"]) == 2
      and len(_parsed["invoices"]) == 4, f"inv={len(_parsed['invoices'])} def={len(_parsed['deferred'])}")

for _inv in _parsed["deferred"]:
    _insert_raw_ledger(_conn, _inv, _batch_id, "invoice")
for _inv in _parsed["invoices"]:
    _insert_raw_ledger(_conn, _inv, _batch_id, "invoice")
for _p in _parsed["prepayments"]:
    _insert_raw_ledger(_conn, _p, _batch_id, "prepayment")

# 本批 collection：P4 显式逐人收款（问题行/右栏编辑那种），P1 备注推导（person_name 空）
_conn.execute(
    "INSERT INTO collection (invoice_no, amount, receipt_date, person_name, source, import_batch_id) "
    "VALUES ('P4', 4000.0, '2025-01-25', '胡坚', 'import', ?)", (_batch_id,))
_conn.execute(
    "INSERT INTO collection (invoice_no, amount, receipt_date, person_name, source, import_batch_id) "
    "VALUES ('P1', 10000.0, '2025-01-31', '', 'import', ?)", (_batch_id,))
_conn.commit()
_conn.close()


# ---------------------------------------------------------------- B1 分桶与勾稽
def _by_no(items):
    return {i["invoice_no"]: i for i in items}


d = RB.rebuild_period_data("2025-01", in_library={"P6"})
check("B1 批次溯源三要素随 data 带出",
      d["batch_id"] == _batch_id and d["file_name"] == "2025.1台账.xlsx"
      and d["path"] == "测试/存档/2025.1台账.xlsx", f"{d['batch_id']}/{d['file_name']}")
check("B1 sheet1/2 → invoices（4 张）", len(d["invoices"]) == 4,
      f"{[i['invoice_no'] for i in d['invoices']]}")
check("B1 sheet3 → deferred（2 张）", len(d["deferred"]) == 2,
      f"{[i['invoice_no'] for i in d['deferred']]}")
check("B1 sheet4 → prepayments（1 条）", len(d["prepayments"]) == 1)
check("B1 problems 恒为空（问题行不入镜表）", d["problems"] == [])
check("B1 backfills 恒为空（补录已写进 invoice/manual）", d["backfills"] == [])
check("B1 sheet_totals 含 sheet3（源口径）",
      d["sheet_totals"] == {"sheet1": 8000.0, "sheet2": 6000.0, "sheet3": 8000.0},
      f"{d['sheet_totals']}")
check("B1 sheet12_total = sheet1 + sheet2", d["sheet12_total"] == 14000.0, f"{d['sheet12_total']}")

# 往返等价：解析结果 ⇄ 重建结果，业务字段逐字段比对
_FIELDS = ("sheet", "sheet_name", "row_no", "invoice_no", "invoice_date", "buyer",
           "total_amount", "handlers", "handler_text", "remark_raw", "remark",
           "case_no", "is_red")
_lp, _rb = _by_no(_parsed["invoices"]), _by_no(d["invoices"])
_diff = [
    f"{no}.{f}: {_lp[no].get(f)!r} != {_rb[no].get(f)!r}"
    for no in _lp for f in _FIELDS
    if _lp[no].get(f) != _rb[no].get(f)
]
check("B2 往返等价：invoices 业务字段逐字段一致", not _diff and set(_lp) == set(_rb),
      "; ".join(_diff[:4]))
_lpd, _rbd = _by_no(_parsed["deferred"]), _by_no(d["deferred"])
_ddiff = [
    f"{no}.{f}: {_lpd[no].get(f)!r} != {_rbd[no].get(f)!r}"
    for no in _lpd for f in _FIELDS
    if _lpd[no].get(f) != _rbd[no].get(f)
]
check("B2 往返等价：deferred 业务字段逐字段一致", not _ddiff and set(_lpd) == set(_rbd),
      "; ".join(_ddiff[:4]))
_PP_FIELDS = ("sheet", "sheet_name", "row_no", "received_date", "buyer", "amount",
              "person_text", "remark", "case_no")
_pdiff = [
    f"{f}: {_parsed['prepayments'][0].get(f)!r} != {d['prepayments'][0].get(f)!r}"
    for f in _PP_FIELDS
    if len(_parsed["prepayments"]) == len(d["prepayments"]) == 1
    and _parsed["prepayments"][0].get(f) != d["prepayments"][0].get(f)
]
check("B2 往返等价：prepayments 业务字段逐字段一致",
      len(d["prepayments"]) == len(_parsed["prepayments"]) == 1 and not _pdiff,
      "; ".join(_pdiff))

check("B3 sheet1 空备注兜底被重建（收款认定=全额）",
      _by_no(d["invoices"])["P1"]["remark"]["receipts"] == [("2025-01", 0.0)])
check("B4 前置：fixture 的 sheet2 备注确实是纯日期（parse_remark 认账）→ 断言非空转",
      bool(parse_remark("25.1.20", default_year=2025).get("pure_date")))
check("B4 sheet2 纯日期被降级（未收）",
      _by_no(d["invoices"])["P3"]["remark"]["pure_date"] is None
      and _by_no(d["invoices"])["P3"]["remark"]["receipts"] == [])
check("B5 sheet3 纯日期保留为收款日期",
      (_by_no(d["deferred"])["P5"]["remark"]["pure_date"] or "").startswith("2025-02"))
check("B6 deferred need_backfill：缺号 True / 已在库 False",
      _by_no(d["deferred"])["P5"]["need_backfill"] is True
      and _by_no(d["deferred"])["P6"]["need_backfill"] is False)
check("B7 显式逐人收款从 collection 读回（备注推不出收款时）",
      _by_no(d["invoices"])["P4"].get("split_receipts") == [("胡坚", 4000.0, "2025-01-25")],
      f"{_by_no(d['invoices'])['P4'].get('split_receipts')}")
check("B7 备注能推出收款的行**不注入**（否则逐人分摊显示会被打成 0）",
      "split_receipts" not in _by_no(d["invoices"])["P1"],
      f"{_by_no(d['invoices'])['P1'].get('split_receipts')}")
check("B7 镜表不存原始行 → header/raw_row 为空（由存档文件兜底）",
      all(i["header"] == [] and i["raw_row"] == [] for i in d["invoices"]))

# 无批次账期 → 空骨架
d_empty = RB.rebuild_period_data("2030-01")
check("B8 无 active 台账批次 → 空骨架（界面显示空表而非报错）",
      d_empty["invoices"] == [] and d_empty["deferred"] == [] and d_empty["batch_id"] is None)
check("B8 空骨架字段齐全（invoices/deferred/prepayments/problems/sheet_totals）",
      all(k in d_empty for k in ("invoices", "deferred", "prepayments", "problems",
                                 "sheet_totals", "sheet12_total", "backfills")))
check("B8 active_ledger_batch 无批次 → None", RB.active_ledger_batch("2030-01") is None)
check("B8 active_ledger_batch 有批次 → 取到", (RB.active_ledger_batch("2025-01") or {}).get("id") == _batch_id)

# 存档不可读 → 空行（不抛异常）
check("B9 read_ledger_row 存档缺失 → ([], [])",
      RB.read_ledger_row("202501sheet1", 2, "不存在的路径.xlsx") == ([], []))
check("B9 read_ledger_row 参数不全 → ([], [])",
      RB.read_ledger_row("", 2, "x.xlsx") == ([], []) and RB.read_ledger_row("s", 0, "x.xlsx") == ([], []))


# ==================================================================== C) 对话框 post 模式
# ---------------------------------------------------------------- C1 pre（默认）不变
_pre = UnifiedImportDialog(
    {"period": "2025-01", "invoices": [], "prepayments": [], "problems": [],
     "sheet_totals": {}, "sheet12_total": 0.0}, "2025-01", STAFF)
check("C1 默认 mode=pre", _pre.mode == "pre")
check("C1 pre 模式「确认入库」可见", not _pre.btn_confirm.isHidden())
check("C1 pre 模式底部仍是「取消」", _pre.btn_cancel.text() == "取消")

# ---------------------------------------------------------------- C2 post 只读
d2 = RB.rebuild_period_data("2025-01")
dlg = UnifiedImportDialog(mode="post")
check("C2 mode=post", dlg.mode == "post")
check("C2 post 模式隐藏「确认入库」", dlg.btn_confirm.isHidden())
check("C2 post 模式「取消」改「关闭」", dlg.btn_cancel.text() == "关闭")

dlg.load_period("2025-01")
check("C2 load_period 载入该期数据（staff 从库读 → 判定可信）",
      len(dlg._rows) == 6, f"rows={len(dlg._rows)}")
check("C2 标题带账期", "2025-01" in dlg.windowTitle(), dlg.windowTitle())
check("C2 溯源 path 取批次存档路径", dlg._path == "测试/存档/2025.1台账.xlsx", dlg._path)
# 默认筛选 = 待补录（与 pre 模式一致）—— 本期只有 P5 缺号 → 1 行
check("C2 默认筛选仍是「待补录」", dlg.table.rowCount() == 1, f"rowCount={dlg.table.rowCount()}")

# ⚠️ 已知坑：默认筛选是「待补录」，此时按下标选行会选不中 → 断言前先切「全部」
dlg._grp.button(FILTER_ALL).setChecked(True)
dlg._render()
check("C2 「全部」= 6 行（4 发票 + 2 应收账款）",
      dlg.table.rowCount() == 6, f"rowCount={dlg.table.rowCount()}")

def _row_no_of(r) -> str:
    """复核页行 dict → 票号（发票行取 ev，应收行取 deferred，问题行取 problem）。"""
    if r["kind"] == "invoice":
        return (r.get("ev") or {}).get("invoice_no") or ""
    if r["kind"] == "deferred":
        return (r.get("deferred") or {}).get("invoice_no") or ""
    return (r.get("problem") or {}).get("invoice_no") or ""


_status = {_row_no_of(r): r["status"] for r in dlg._rows}
check("C2 sheet3「已在库」行 → 高置信（导入后无待办）", _status.get("P6") == "高置信",
      f"{_status}")
check("C2 sheet3 缺号行 → 仍是「待补录」（去补录页处理）", _status.get("P5") == "待补录",
      f"{_status}")
check("C2 发票行无疑问 → 高置信", _status.get("P1") == "高置信", f"{_status}")

# 原因文案：待补录 = 需补录原票；已在库 = 无疑问（不再是「请确认收款」）
def _reason_of(dlg_, no):
    for r in dlg_._rows:
        if _row_no_of(r) == no:
            return dlg_._row_field(r, "reason")
    return None


check("C2 原因列：缺号 → 需补录原票", _reason_of(dlg, "P5") == REASON_BACKFILL,
      _reason_of(dlg, "P5"))
check("C2 原因列：已在库 → ✓ 系统判定无疑问", _reason_of(dlg, "P6") == REASON_NO_DOUBT,
      _reason_of(dlg, "P6"))

# 只读契约：右栏表单锁死 + 行内写按钮全隐藏
def _select(d, r):
    i = d._rows.index(r)
    d.table.setCurrentCell(i, 0)
    d.table.selectRow(i)
    d._load_right()


_leaked: list = []
_bf_leaked: list = []
for _r in list(dlg._rows):
    _select(dlg, _r)
    _no = _row_no_of(_r)
    for _btn, _nm in ((dlg.btn_save, "保存修改"), (dlg.btn_refix, "重新修正"),
                      (dlg.btn_confirm_row, "确认"), (dlg.btn_edit, "编辑")):
        if not _btn.isHidden():
            _leaked.append(f"{_no}:{_nm}")
    if not dlg.btn_backfill.isHidden():
        _bf_leaked.append(_no)
check("C2 post 模式：所有行的写操作按钮（保存/重新修正/确认/编辑）全部隐藏",
      not _leaked, f"{_leaked}")
check("C2 post 模式：所有行的「补录原票」入口隐藏（补录要写库，去补录页）",
      not _bf_leaked, f"{_bf_leaked}")

# 右栏表单只读（post）：选中发票行后输入框 ReadOnly、子表禁编辑
_select(dlg, next(r for r in dlg._rows if _row_no_of(r) in ("P1", "P2", "P3", "P4")))
check("C2 post 模式右栏表单只读（输入框 ReadOnly + 子表禁编辑）",
      dlg.fix_panel._readonly is True and dlg.fix_panel.inv_date.isReadOnly() is True
      and dlg.fix_panel.btn_add.isEnabled() is False,
      f"ro={dlg.fix_panel._readonly} date={dlg.fix_panel.inv_date.isReadOnly()} "
      f"add={dlg.fix_panel.btn_add.isEnabled()}")

# 建面板即锁：即使默认筛选空表、没有行被选中，post 模式面板也是只读的
_lone = UnifiedImportDialog(mode="post")
_lone.load_period("2030-01")          # 无批次 → 空表 → 无行可选
check("C2 post 模式空表时面板仍锁死（不依赖是否选中行）",
      _lone.fix_panel._readonly is True)

# accept() 在 post 模式**不写库**
_before = repr(dlg._data.get("invoices")) + repr(dlg._data.get("deferred"))
_MB.calls.clear()
dlg.accept()
check("C2 post 模式 accept() 一行不写（_data 原样）",
      repr(dlg._data.get("invoices")) + repr(dlg._data.get("deferred")) == _before)
check("C2 post 模式 accept() 不弹「尚有未处理的行」",
      not any("尚有未处理的行" in c[1] for c in _MB.calls), f"{_MB.calls}")

# 双击「查看原始台账行」：镜像无原始行 → 走存档文件；存档不存在 → 友好提示不崩
_MB.calls.clear()
_select(dlg, next(r for r in dlg._rows if _row_no_of(r) in ("P1", "P2", "P3", "P4")))
dlg._show_source()
check("C2 存档不可读 → 提示「无原始行」而非抛异常",
      any("没有对应的原始台账行" in c[1] for c in _MB.calls), f"{_MB.calls}")

# 清空数据后 load_period 空账期 → 空表不报错
dlg.load_period("2030-01")
check("C2 空账期 load_period → 0 行且不抛异常", dlg.table.rowCount() == 0)

# ==================================================================== D) 批 3-1 导入时确认留痕
# 独立账期 2025-02 → 不扰动 B/C 的计数断言。
# ⚠️ 低置信原因必须选「**能过写前校验**」的那一类：`validate_ledger_before_write` 只查
#    「经办人是否在花名册」，所以「不在花名册」在真实导入里**到不了「确认入库」**（会被拦）；
#    而「无经办人」handlers 为空 → 校验直接通过 ⇒ 才是真实的「导入时确认」场景。
from app.engine import import_confirm as _IC  # noqa: E402

_ROWS_D = [
    _HDR_INV,
    ["1", "2025.2.6", "Q2", "壬公司", "2000", "周立生2000", "", ""],   # 无疑问 → 高置信
    ["2", "2025.2.7", "Q3", "癸公司", "3000", "", "", ""],             # 无经办人 → 待确认（已确认）
    ["3", "2025.2.8", "Q4", "子公司", "4000", "", "", ""],             # 无经办人 → 待确认（未确认）
]
_conn2 = _db.get_conn()
_conn2.execute(
    "INSERT INTO import_batch (batch_type, period, file_name, archive_path, imported_at, status) "
    "VALUES ('ledger','2025-02','2025.2台账.xlsx','测试/存档/2025.2台账.xlsx','2025-03-01 10:00','active')")
_bid2 = _conn2.execute("SELECT id FROM import_batch ORDER BY id DESC LIMIT 1").fetchone()[0]
_items_d, _probs_d = _parse_invoice_sheet(_ROWS_D, "sheet1", "202502sheet1", "2025-02")
check("D0 构造：0 问题行（无经办人照常成行，不进问题桶）",
      _probs_d == [], f"{[p['reason'] for p in _probs_d]}")
check("D0 构造：Q3/Q4 的 handlers 确实为空（低置信原因 = 无经办人）",
      _by_no(_items_d)["Q3"]["handlers"] == [] and _by_no(_items_d)["Q4"]["handlers"] == [],
      f"{[_by_no(_items_d)[n]['handlers'] for n in ('Q3', 'Q4')]}")
for _i in _items_d:
    _insert_raw_ledger(_conn2, _i, _bid2, "invoice")
# 只有 Q3 在导入那一刻被点过「确认」；Q4 没点（用于验证「逐票判定」而非一刀切）
_IC.save_confirmations(_conn2, "2025-02", [{"invoice_no": "Q3", "note": "无经办人"}])
_conn2.commit()
_conn2.close()

_d2 = RB.rebuild_period_data("2025-02")
check("D1 读侧：留痕随 rebuild 带出（票号 → 备注）",
      _d2["confirmations"] == {"Q3": "无经办人"}, f"{_d2['confirmations']}")

# --- pre 模式：留痕**不生效**。确认在 pre 是实时的 `_confirmed` 下标集合；
#     若 pre 也读留痕，「覆盖式重导同一账期」时上一批的旧确认会提前吞掉本批的疑问。
_pre2 = UnifiedImportDialog(_d2, "2025-02", STAFF)
_st_pre = {_row_no_of(r): r["status"] for r in _pre2._rows}
check("D2 pre 模式：留痕不生效（Q3/Q4 都仍是「待确认」）",
      _st_pre.get("Q3") == "待确认" and _st_pre.get("Q4") == "待确认", f"{_st_pre}")
check("D2 pre 模式也不打标注（is_import_confirmed 恒 False）",
      all(not r.get("is_import_confirmed") for r in _pre2._rows))

# --- post 模式：已确认 → 高置信 + 标注；未确认 → 仍「待确认」
_dlg2 = UnifiedImportDialog(mode="post")
_dlg2.load_period("2025-02")
_d_row = {_row_no_of(r): r for r in _dlg2._rows}
check("D3 post 模式：导入时已确认的行 → 高置信（假待办消失）",
      _d_row["Q3"]["status"] == "高置信", f"{_d_row['Q3']['status']}")
check("D3 post 模式：同为「无经办人」但未确认的行 → 仍「待确认」（逐票判定，不一刀切）",
      _d_row["Q4"]["status"] == "待确认", f"{_d_row['Q4']['status']}")
check("D3 post 模式：无疑问行不受影响", _d_row["Q2"]["status"] == "高置信")
check("D3 post 模式：「待确认」筛选里已无 Q3",
      all(_row_no_of(r) != "Q3" for r in _dlg2._rows if r["status"] == "待确认"),
      f"{[(_row_no_of(r), r['status']) for r in _dlg2._rows if r['status'] == '待确认']}")

check("D4 标注：已确认行带 is_import_confirmed + 留痕备注",
      _d_row["Q3"].get("is_import_confirmed") is True
      and _d_row["Q3"].get("import_confirm_note") == "无经办人",
      f"{_d_row['Q3'].get('is_import_confirmed')}/{_d_row['Q3'].get('import_confirm_note')!r}")
check("D4 标注：未确认行不带标记",
      _d_row["Q4"].get("is_import_confirmed") is False
      and _d_row["Q4"].get("import_confirm_note") == "",
      f"{_d_row['Q4'].get('is_import_confirmed')}/{_d_row['Q4'].get('import_confirm_note')!r}")
_r_q3 = _dlg2._row_field(_d_row["Q3"], "reason")
check("D4 原因列：疑问原文照旧展示 + 追加「导入时已确认」（绝不隐藏原文）",
      "无经办人" in _r_q3 and REASON_IMPORT_CONFIRMED in _r_q3, _r_q3)
check("D4 原因列：未确认行不加标注",
      REASON_IMPORT_CONFIRMED not in _dlg2._row_field(_d_row["Q4"], "reason"),
      _dlg2._row_field(_d_row["Q4"], "reason"))

# --- _merged_data：把本次点过「确认」的发票行收集成 confirmations（随台账同一事务落库）
_pre3 = UnifiedImportDialog(_d2, "2025-02", STAFF)
_q3 = next(r for r in _pre3._rows if _row_no_of(r) == "Q3")
_pre3._confirmed.add(_q3["work_idx"])          # 模拟用户点了「确认」
_md = _pre3._merged_data()
check("D5 _merged_data：确认过的行 → confirmations 一条（票号 + 原因备注）",
      len(_md["confirmations"]) == 1
      and _md["confirmations"][0]["invoice_no"] == "Q3"
      and "无经办人" in _md["confirmations"][0]["note"],
      f"{_md['confirmations']}")
check("D5 _merged_data：**未**确认的行不进留痕",
      all(c["invoice_no"] != "Q4" for c in _md["confirmations"]))

# ==================================================================== E) 批 3-2 分摊取库侧真值
# 背景：`raw_ledger` **刻意**只存台账原文（`importer._insert_raw_ledger` 的口径：好让
# 「源 ⇄ 库」逐字对照）⇒ 导入时在复核页手工改过的分摊，导入后重解析原文只会拿回**旧值**；
# 原文本就解析不出的问题行更是拿回**空值**（→ 界面重报「无经办人」等假待办）。
# 真值在 `charge_detail`，且只有 `import_batch_id = 本台账批次` 的那批可证明是本次写入的。
#
# ⚠️ 本节必须在 B2「往返等价」**之后**跑：它会往 2025-01 的票上补 charge_detail，
#    之后重建就不再与解析结果逐字段等价（那正是本节要证明的效果）。顺序不可调换。
_conn3 = _db.get_conn()


def _set_charge(no, rows, batch_id):
    """把某票的 charge_detail 置为 rows（(姓名, 金额)），批次号可控 → 供反向探针造场景。"""
    _conn3.execute("DELETE FROM charge_detail WHERE invoice_no=?", (no,))
    for _nm, _amt in rows:
        _conn3.execute(
            "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source, "
            "import_batch_id) VALUES (?,?,?,'import',?)", (no, _nm, _amt, batch_id))
    _conn3.commit()


# --- E1 主场景：P1 台账原文「周立生」，导入时人工改成「陈娟」（同额）
_set_charge("P1", [("陈娟", 10000.0)], _batch_id)
_d3 = RB.rebuild_period_data("2025-01")
_p1 = _by_no(_d3["invoices"])["P1"]
check("E1 分摊取库侧真值（人工修正后的值，不再是原文旧值）",
      _p1["handlers"] == [("陈娟", 10000.0)], f"{_p1['handlers']}")
check("E1 原文照旧保留在 handler_text（〔源填写〕不丢）",
      _p1["handler_text"] == "周立生", f"{_p1['handler_text']!r}")
check("E1 标记 handlers_from_lib=True（供「源 ⇄ 库」比对区分来源）",
      _p1.get("handlers_from_lib") is True)
check("E1 注入后仍与行金额勾稽（绝不引入「分摊合计≠价税合计」）",
      abs(sum(a for _n, a in _p1["handlers"]) - _p1["total_amount"]) <= 0.01)
check("E1 金额本就存的是修正值（镜表 amount_num = 修正后金额，不受本节影响）",
      _p1["total_amount"] == 10000.0, f"{_p1['total_amount']}")

# --- E2 端到端：post 界面「经办人分摊」列 = 库值 + 〔源填写〕原文
_dlg3 = UnifiedImportDialog(mode="post")
_dlg3.load_period("2025-01")
_dlg3._grp.button(FILTER_ALL).setChecked(True)
_dlg3._render()
_r_p1 = next(r for r in _dlg3._rows if _row_no_of(r) == "P1")
_txt_p1 = _dlg3._row_field(_r_p1, "handler")
check("E2 post 界面：分摊列显示库值 + 〔源填写〕台账原文",
      "陈娟" in _txt_p1 and "〔源填写〕" in _txt_p1 and "周立生" in _txt_p1, _txt_p1)

# --- E3 反向探针①：charge_detail 属**别的批次** → 一律不采用（只认本批次）
_set_charge("P1", [("陈娟", 10000.0)], _batch_id + 999)
_p1b = _by_no(RB.rebuild_period_data("2025-01")["invoices"])["P1"]
check("E3 反向：非本批次 charge_detail 不采用（回退原文解析）",
      _p1b["handlers"] == [("周立生", 10000.0)]
      and _p1b.get("handlers_from_lib") is False, f"{_p1b['handlers']}")

# --- E4 反向探针②：本批次但合计≠行金额 → 不采用（宁可显示原文，也不造反疑问）
_set_charge("P1", [("陈娟", 9999.0)], _batch_id)
_d4 = RB.rebuild_period_data("2025-01")
_p1c = _by_no(_d4["invoices"])["P1"]
check("E4 反向：库值不勾稽 → 不采用（回退原文解析）",
      _p1c["handlers"] == [("周立生", 10000.0)]
      and _p1c.get("handlers_from_lib") is False, f"{_p1c['handlers']}")
_dlg4 = UnifiedImportDialog(mode="post")
_dlg4.load_period("2025-01")
_dlg4._grp.button(FILTER_ALL).setChecked(True)
_dlg4._render()
_r_p1c = next(r for r in _dlg4._rows if _row_no_of(r) == "P1")
check("E4 端到端：不采用时原因列也不出现「分摊合计≠价税合计」（假待办绝不新增）",
      "分摊合计" not in _dlg4._row_field(_r_p1c, "reason"),
      _dlg4._row_field(_r_p1c, "reason"))

# --- E5 反向探针③：sheet3（应收账款）行不取**别的批次**的 charge_detail
#     理由：镜表 sheet3 行是「应收账款视角」，别的批次的 charge_detail 是「发票视角」
#     （批 0 §9.6：两者语义不同，不得混用）。真实 12 期库里 sheet3 198 行中有 121 行
#     的票在库，但都不属本批次 → 一律回退原文解析。
_set_charge("P6", [("陈娟", 5000.0)], _batch_id + 1)
_d5 = RB.rebuild_period_data("2025-01")
_p6 = _by_no(_d5["deferred"])["P6"]
check("E5 反向：sheet3 行不取非本批次的 charge_detail（仍按原文解析）",
      _p6["handlers"] == [("柳立中", 5000.0)]
      and _p6.get("handlers_from_lib") is False, f"{_p6['handlers']}")

# --- E6 反向探针④：没有 charge_detail 的票 → 完整回退原文（零扰动，回归保护）
check("E6 反向：无库值 → 完全按原文解析（P2 不受本节影响）",
      _by_no(_d5["invoices"])["P2"]["handlers"] == [("陈娟", -2000.0)]
      and _by_no(_d5["invoices"])["P2"].get("handlers_from_lib") is False,
      f"{_by_no(_d5['invoices'])['P2']['handlers']}")

# 恢复真实库路径
_db.DB_PATH = _real_db

# ==================================================================== 汇总
_failed = [n for n, ok, _ in results if not ok]
print()
for n, ok, extra in results:
    if not ok:
        print(f"  FAIL {n} {extra}")
print(f"{len(results) - len(_failed)}/{len(results)} passed")
sys.exit(1 if _failed else 0)
