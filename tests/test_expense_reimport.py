"""同账期重导（feature 3）接线测试 —— 内存库。

聚焦「接线 + cancel/overwrite 语义」，不重复测 T1 的 diff 内核
（内核见 tests/test_expense_validation.py）。

覆盖：
- 首导（无 active 批次）：回调不被调用，静默写库（零回归）；
- 同账期二次导入、有差异：回调被调用；
  · cancel → 抛 ImportError_、库完全不变（旧批次仍 active、expense_ledger 行数/值不变）；
  · overwrite → 旧批次 rolled_back、旧行被新行整体替换；
- 完全相同重导：diff 为空 → 回调不被调用 → 静默覆盖（零回归）；
- 非 UI 调用方不传回调：有差异也静默覆盖（零回归）。

运行：python tests/test_expense_reimport.py
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db as _db_mod  # noqa: E402
from app.importer import importer  # noqa: E402
from app.importer.excel_reader import ImportError_  # noqa: E402
from app.engine import account_subject as asub  # noqa: E402
from app.engine import expense_cat as ec  # noqa: E402

OK, FAILS = 0, []


class _ConnProxy:
    """把 sqlite3 连接包成「get_conn 的返回值」，让 importer 用内存库。"""

    def __init__(self, c):
        self._c = c

    def execute(self, *a, **k):
        return self._c.execute(*a, **k)

    def executescript(self, *a, **k):
        return self._c.executescript(*a, **k)

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        pass

    def __getattr__(self, n):
        return getattr(self._c, n)


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


# 二次导入（新文件）内容：seq 1/2 改、seq 3 新增
NEW_ITEMS = [
    {"period": "2025-01", "seq": 1, "exp_date": "2025-01-10", "name": "新A",
     "ticket_no": "", "handler": "", "actual_handler": "", "expense_amount": 150.0,
     "tax_amount": 10.0, "book_amount": 140.0, "expense_type": "", "voucher_no": "",
     "subject1": "", "subject2": "", "person_type": ""},
    {"period": "2025-01", "seq": 2, "exp_date": "2025-01-11", "name": "新B",
     "ticket_no": "", "handler": "", "actual_handler": "", "expense_amount": 200.0,
     "tax_amount": 0.0, "book_amount": 200.0, "expense_type": "", "voucher_no": "",
     "subject1": "", "subject2": "", "person_type": ""},
    {"period": "2025-01", "seq": 3, "exp_date": "2025-01-12", "name": "新C",
     "ticket_no": "", "handler": "", "actual_handler": "", "expense_amount": 50.0,
     "tax_amount": 0.0, "book_amount": 50.0, "expense_type": "", "voucher_no": "",
     "subject1": "", "subject2": "", "person_type": ""},
]

# 库里已有（旧文件）内容：seq 1/2 与新的不同、seq 9 将删除
OLD_ITEMS = [
    {"period": "2025-01", "seq": 1, "exp_date": "2025-01-10", "name": "原A",
     "ticket_no": "", "handler": "", "actual_handler": "", "expense_amount": 100.0,
     "tax_amount": 10.0, "book_amount": 90.0, "expense_type": "", "voucher_no": "",
     "subject1": "", "subject2": "", "person_type": ""},
    {"period": "2025-01", "seq": 2, "exp_date": "2025-01-11", "name": "原B",
     "ticket_no": "", "handler": "", "actual_handler": "", "expense_amount": 200.0,
     "tax_amount": 0.0, "book_amount": 200.0, "expense_type": "", "voucher_no": "",
     "subject1": "", "subject2": "", "person_type": ""},
    {"period": "2025-01", "seq": 9, "exp_date": "2025-01-19", "name": "原Z",
     "ticket_no": "", "handler": "", "actual_handler": "", "expense_amount": 9.0,
     "tax_amount": 0.0, "book_amount": 9.0, "expense_type": "", "voucher_no": "",
     "subject1": "", "subject2": "", "person_type": ""},
]


class _OverwriteRec:
    def __init__(self):
        self.calls = []

    def __call__(self, diff):
        self.calls.append(diff)
        return "overwrite"


class _CancelRec:
    def __init__(self):
        self.calls = []

    def __call__(self, diff):
        self.calls.append(diff)
        return "cancel"


def _build_env(initial_items, reimport_items):
    """构造内存库 + 把 importer 全部落库/解析/校验旁路到测试可控状态。

    返回 (real_conn, proxy, saved, old_batch_id)。
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    proxy = _ConnProxy(conn)

    saved = {}
    saved["app_db_get_conn"] = _db_mod.get_conn
    saved["imp_get_conn"] = importer.get_conn
    _db_mod.get_conn = lambda: proxy
    importer.get_conn = lambda: proxy

    _db_mod.init_db()

    old_bid = None
    if initial_items:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            "INSERT INTO import_batch (batch_type, period, file_name, status, imported_at) "
            "VALUES ('expense','2025-01','old.xlsx','active',?)", (now,))
        old_bid = cur.lastrowid
        for it in initial_items:
            conn.execute(
                "INSERT INTO expense_ledger (period, seq, exp_date, name, ticket_no, handler, "
                "actual_handler, expense_amount, tax_amount, book_amount, expense_type, "
                "voucher_no, subject1, subject2, source, import_batch_id, person_type) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (it["period"], it["seq"], it["exp_date"], it["name"], it["ticket_no"],
                 it["handler"], it["actual_handler"], it["expense_amount"], it["tax_amount"],
                 it["book_amount"], it["expense_type"], it["voucher_no"], it["subject1"],
                 it["subject2"], "import", old_bid, it["person_type"]))
        conn.commit()

    # 解析 + 校验全部旁路（聚焦接线，不重复测 T1 校验）
    saved["parse"] = importer.parse_expense_file
    importer.parse_expense_file = lambda path, period: list(reimport_items)
    saved["vh"] = importer._validate_handler_names
    importer._validate_handler_names = lambda c, names, ctx: []
    saved["snap"] = importer._auto_snapshot
    importer._auto_snapshot = lambda: None
    saved["arch"] = importer._archive_file
    importer._archive_file = lambda *a, **k: "archive/dummy.xlsx"
    saved["ck"] = ec.check_unknown
    ec.check_unknown = lambda c, etypes: set()
    saved["pe"] = ec.validate_public_exclusive
    ec.validate_public_exclusive = lambda c, items: []
    saved["vs"] = asub.validate_ledger_subjects
    asub.validate_ledger_subjects = lambda items, c: set()

    return conn, proxy, saved, old_bid


def _restore(saved):
    _db_mod.get_conn = saved["app_db_get_conn"]
    importer.get_conn = saved["imp_get_conn"]
    importer.parse_expense_file = saved["parse"]
    importer._validate_handler_names = saved["vh"]
    importer._auto_snapshot = saved["snap"]
    importer._archive_file = saved["arch"]
    ec.check_unknown = saved["ck"]
    ec.validate_public_exclusive = saved["pe"]
    asub.validate_ledger_subjects = saved["vs"]


def main() -> int:
    # ---- 首导（无 active 批次）：回调不被调用，静默写库（零回归）----
    conn, _, saved, _ = _build_env(None, NEW_ITEMS)
    try:
        rec = _OverwriteRec()
        r = importer.import_expense_file("new.xlsx", "2025-01", on_reimport_diff=rec)
        check("首导(无active批次)回调不被调用", len(rec.calls) == 0)
        check("首导返回 count=3", r["count"] == 3, str(r))
        rows = conn.execute("SELECT name FROM expense_ledger ORDER BY seq").fetchall()
        check("首导写库 3 行", [x["name"] for x in rows] == ["新A", "新B", "新C"],
              [x["name"] for x in rows])
    finally:
        _restore(saved)

    # ---- 同账期二次导入、有差异：cancel → 抛异常 + 库完全不变 ----
    conn, _, saved, old_bid = _build_env(OLD_ITEMS, NEW_ITEMS)
    try:
        rec = _CancelRec()
        raised = False
        errmsg = ""
        try:
            importer.import_expense_file("new.xlsx", "2025-01", on_reimport_diff=rec)
        except ImportError_ as e:
            raised = True
            errmsg = str(e)
        except Exception as e:  # noqa: BLE001
            raised = True
            errmsg = f"非预期异常: {e!r}"
        check("cancel 抛 ImportError_ 中止", raised and "已取消" in errmsg, errmsg)
        check("cancel 时回调被调用", len(rec.calls) == 1, len(rec.calls))
        status = conn.execute(
            "SELECT status FROM import_batch WHERE id=?", (old_bid,)).fetchone()["status"]
        check("cancel 后旧批次仍 active", status == "active", status)
        rows = conn.execute(
            "SELECT name FROM expense_ledger WHERE import_batch_id=? ORDER BY seq",
            (old_bid,)).fetchall()
        check("cancel 后 expense_ledger 行数不变(3)", len(rows) == 3, len(rows))
        check("cancel 后旧行值不变", [x["name"] for x in rows] == ["原A", "原B", "原Z"],
              [x["name"] for x in rows])
        check("cancel 后无新增批次",
              conn.execute("SELECT count(*) FROM import_batch").fetchone()[0] == 1)
    finally:
        _restore(saved)

    # ---- 同账期二次导入、有差异：overwrite → 旧批次 rolled_back + 旧行被替换 ----
    conn, _, saved, old_bid = _build_env(OLD_ITEMS, NEW_ITEMS)
    try:
        rec = _OverwriteRec()
        r = importer.import_expense_file("new.xlsx", "2025-01", on_reimport_diff=rec)
        check("overwrite 不抛异常", True)
        check("overwrite 时回调被调用", len(rec.calls) == 1, len(rec.calls))
        check("overwrite 返回 count=3", r["count"] == 3, str(r))
        old_status = conn.execute(
            "SELECT status FROM import_batch WHERE id=?", (old_bid,)).fetchone()["status"]
        check("overwrite 后旧批次 rolled_back", old_status == "rolled_back", old_status)
        new_bid = conn.execute(
            "SELECT id FROM import_batch WHERE batch_type='expense' AND period='2025-01' "
            "AND status='active'").fetchone()["id"]
        check("overwrite 后有且仅一个新 active 批次", new_bid != old_bid)
        rows = conn.execute(
            "SELECT name FROM expense_ledger WHERE import_batch_id=? ORDER BY seq",
            (new_bid,)).fetchall()
        check("overwrite 后旧行被新行替换",
              [x["name"] for x in rows] == ["新A", "新B", "新C"], [x["name"] for x in rows])
    finally:
        _restore(saved)

    # ---- 完全相同重导：diff 为空 → 回调不被调用 → 静默覆盖（零回归）----
    conn, _, saved, old_bid = _build_env(NEW_ITEMS, NEW_ITEMS)
    try:
        rec = _OverwriteRec()
        r = importer.import_expense_file("new.xlsx", "2025-01", on_reimport_diff=rec)
        check("完全相同重导回调不被调用", len(rec.calls) == 0, len(rec.calls))
        check("完全相同重导仍写库 count=3", r["count"] == 3, str(r))
    finally:
        _restore(saved)

    # ---- 非 UI 调用方不传回调：有差异也静默覆盖（零回归）----
    conn, _, saved, old_bid = _build_env(OLD_ITEMS, NEW_ITEMS)
    try:
        r = importer.import_expense_file("new.xlsx", "2025-01")
        check("无回调时静默覆盖不抛异常", True)
        check("无回调时写库 count=3", r["count"] == 3, str(r))
        new_bid = conn.execute(
            "SELECT id FROM import_batch WHERE batch_type='expense' AND period='2025-01' "
            "AND status='active'").fetchone()["id"]
        rows = conn.execute(
            "SELECT name FROM expense_ledger WHERE import_batch_id=? ORDER BY seq",
            (new_bid,)).fetchall()
        check("无回调时旧行被新行替换",
              [x["name"] for x in rows] == ["新A", "新B", "新C"], [x["name"] for x in rows])
    finally:
        _restore(saved)

    # ---- 直接单测 _compute_reimport_diff（绕开 import_expense_file 整条）----
    conn, _, saved, old_bid = _build_env(OLD_ITEMS, NEW_ITEMS)
    try:
        # 无 active 批次 → None
        d0 = importer._compute_reimport_diff(conn, "2099-09", NEW_ITEMS)
        check("_compute_reimport_diff(无active批次)=None", d0 is None)
        # 有 active 批次 + 差异 → 非空且分类正确
        d = importer._compute_reimport_diff(conn, "2025-01", NEW_ITEMS)
        check("_compute_reimport_diff(有差异).changed", d is not None and d.changed)
        check("diff.added=[3]", d.added == [3], str(d.added))
        check("diff.removed=[9]", d.removed == [9], str(d.removed))
        check("diff.matched 含 seq=1", any(seq == 1 for seq, _ in d.matched),
              str(d.matched))
    finally:
        _restore(saved)

    conn.close()
    print(f"\n{OK} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
