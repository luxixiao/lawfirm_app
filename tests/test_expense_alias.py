"""费用类型别名（同义归一）单元测试 —— 内存库 + 导入门禁接线。

覆盖：
- 引擎：add_alias 校验（空/超长/canonical 不存在/重名/与规范类型重名）、
  resolve_type（别名→规范名 / 规范名→原样 / 未知→None）、delete_alias、
  list_aliases / aliases_by_canonical、delete_type 级联删别名、
  rename_type 同步别名 canonical、export_all/import_all 别名往返。
- 导入门禁：台账写「公积金」经 resolve_type 归一成「住房公积金」后过门禁并写库；
  真正未知的类型（冥王星费）仍按「不在维护名单」拦截。

运行：python tests/test_expense_alias.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app import db as _db_mod  # noqa: E402
from app.importer import importer  # noqa: E402
from app.importer.excel_reader import ImportError_  # noqa: E402
from app.engine import expense_cat as ec  # noqa: E402
from app.engine.expense_cat import ExpenseCatError  # noqa: E402
from app.engine import account_subject as asub  # noqa: E402

OK, FAILS = 0, []


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


def expect_err(label, fn, exc=ExpenseCatError):
    global OK
    try:
        fn()
        FAILS.append(f"{label}: 未抛出 {exc.__name__}")
    except exc:
        OK += 1
        print(f"[PASS] {label}")


# 标准台账行骨架（导入门禁用）
def _row(seq, expense_type):
    return {"period": "2025-01", "seq": seq, "exp_date": "2025-01-10", "name": f"R{seq}",
            "ticket_no": "", "handler": "", "actual_handler": "", "expense_amount": 10.0,
            "tax_amount": 0.0, "book_amount": 10.0, "expense_type": expense_type,
            "voucher_no": "", "subject1": "", "subject2": "", "person_type": ""}


def main() -> int:
    # ===================== 引擎层 =====================
    conn = make_conn()
    # 规范类型（住房公积金 同时是分类名与规范类型名，符合项目现状）
    ec.add_type("住房公积金", "住房公积金", conn)
    ec.add_type("保险费", "保险费", conn)

    # 1. add_alias 正常
    ec.add_alias("住房公积金", "公积金", conn)
    check("add_alias 生效",
          ec.resolve_type("公积金", conn) == "住房公积金", ec.resolve_type("公积金", conn))

    # 2. add_alias 校验（全部传 conn，避免落到真实库文件）
    expect_err("空别名", lambda: ec.add_alias("住房公积金", "  ", conn))
    expect_err("超长别名", lambda: ec.add_alias("住房公积金", "x" * 31, conn))
    expect_err("canonical 不存在", lambda: ec.add_alias("不存在的类型", "别名", conn))
    expect_err("别名与规范类型重名", lambda: ec.add_alias("保险费", "住房公积金", conn))
    expect_err("别名重复", lambda: ec.add_alias("保险费", "公积金", conn))

    # 3. resolve_type 语义
    check("别名→规范名", ec.resolve_type("公积金", conn) == "住房公积金")
    check("规范名→原样", ec.resolve_type("住房公积金", conn) == "住房公积金")
    check("未知→None", ec.resolve_type("冥王星费", conn) is None)
    check("空→None", ec.resolve_type("", conn) is None)

    # 4. list / by_canonical
    ec.add_alias("住房公积金", "住房积金", conn)
    al = ec.list_aliases(conn)
    check("list_aliases 数量", len(al) == 2, str(al))
    by = ec.aliases_by_canonical(conn)
    check("by_canonical 归集",
          set(by.get("住房公积金", [])) == {"公积金", "住房积金"}, str(by))

    # 5. delete_alias
    ec.delete_alias("住房积金", conn)
    check("delete_alias 生效",
          ec.resolve_type("住房积金", conn) is None)
    expect_err("删除不存在的别名", lambda: ec.delete_alias("不存在的别名", conn))

    # 6. delete_type 级联删别名
    ec.add_alias("保险费", "社保", conn)
    ec.delete_type("保险费", conn)
    check("删类型级联删别名", ec.resolve_type("社保", conn) is None)
    check("删类型本身生效", "保险费" not in ec.get_map(conn))

    # 7. rename_type 同步别名 canonical
    conn2 = make_conn()
    ec.add_type("公积金", "住房公积金", conn2)  # 这里让「公积金」当规范类型演示改名
    ec.add_alias("公积金", "GJJ", conn2)
    ec.rename_type("公积金", "住房公积金", conn2)
    check("改名后原类型名不可用", ec.resolve_type("公积金", conn2) is None)
    check("改名后新类型名可用", ec.resolve_type("住房公积金", conn2) == "住房公积金")
    check("别名 canonical 跟随改名", ec.resolve_type("GJJ", conn2) == "住房公积金")

    # 8. export_all / import_all 别名往返
    conn3 = make_conn()
    ec.add_type("住房公积金", "住房公积金", conn3)
    ec.add_type("保险费", "保险费", conn3)
    ec.add_alias("住房公积金", "公积金", conn3)
    ec.add_alias("保险费", "社保", conn3)
    data = ec.export_all(conn3)
    check("export 含别名", len(data.get("aliases", [])) == 2, str(data.get("aliases")))

    conn4 = make_conn()
    ec.import_all(data, conn4)
    by4 = ec.aliases_by_canonical(conn4)
    check("import 后别名复原",
          sorted(by4.get("住房公积金", [])) == ["公积金"]
          and sorted(by4.get("保险费", [])) == ["社保"], str(by4))

    # import 校验：别名 canonical 不存在应拦截
    bad = {"categories": [{"name": "住房公积金", "note": "", "sort_order": 1}],
           "types": [{"expense_type": "住房公积金", "category": "住房公积金", "sort_order": 1}],
           "aliases": [{"alias": "公积金", "canonical": "不存在的类型"}]}
    expect_err("导入别名指向不存在的规范类型应拦截",
               lambda: ec.import_all(bad, make_conn()))

    # ===================== 导入门禁接线 =====================
    # 复用 test_expense_reimport 的旁路手法，但**保留真实的 check_unknown**，
    # 以验证「别名归一」发生在门禁之前。
    class _ConnProxy:
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

    def build_env(items):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        saved = {}
        _db_mod.get_conn = lambda: _ConnProxy(c)
        importer.get_conn = lambda: _ConnProxy(c)
        _db_mod.init_db()
        # 规范类型必须存在，别名才能归一到它；同时登记「公积金→住房公积金」别名
        ec.add_type("住房公积金", "住房公积金", c)
        ec.add_alias("住房公积金", "公积金", c)
        saved["parse"] = importer.parse_expense_file
        importer.parse_expense_file = lambda path, period: list(items)
        saved["vh"] = importer._validate_handler_names
        importer._validate_handler_names = lambda c2, names, ctx: []
        saved["snap"] = importer._auto_snapshot
        importer._auto_snapshot = lambda: None
        saved["arch"] = importer._archive_file
        importer._archive_file = lambda *a, **k: "archive/dummy.xlsx"
        saved["pe"] = ec.validate_public_exclusive
        ec.validate_public_exclusive = lambda c2, items: []
        saved["vs"] = asub.validate_ledger_subjects
        asub.validate_ledger_subjects = lambda items, c2: set()
        return c, saved

    def restore(saved):
        importer.parse_expense_file = saved["parse"]
        importer._validate_handler_names = saved["vh"]
        importer._auto_snapshot = saved["snap"]
        importer._archive_file = saved["arch"]
        ec.validate_public_exclusive = saved["pe"]
        asub.validate_ledger_subjects = saved["vs"]
        _db_mod.get_conn = orig_app_get
        importer.get_conn = orig_imp_get

    orig_app_get = _db_mod.get_conn
    orig_imp_get = importer.get_conn

    # ---- 全可归一：公积金→住房公积金 过门禁并写库 ----
    c, saved = build_env([_row(1, "公积金"), _row(2, "住房公积金")])
    try:
        r = importer.import_expense_file("x.xlsx", "2025-01")
        check("门禁放行（别名已归一）", r["count"] == 2, str(r))
        rows = c.execute(
            "SELECT expense_type FROM expense_ledger ORDER BY seq").fetchall()
        check("写库为规范名 公积金→住房公积金",
              [x["expense_type"] for x in rows] == ["住房公积金", "住房公积金"],
              [x["expense_type"] for x in rows])
    finally:
        restore(saved)

    # ---- 真正未知类型仍被拦截 ----
    c, saved = build_env([_row(1, "冥王星费")])
    raised = False
    try:
        try:
            importer.import_expense_file("x.xlsx", "2025-01")
        except ImportError_ as e:
            raised = True
            check("未知类型被门禁拦截", "不在维护名单" in str(e), str(e))
    finally:
        restore(saved)
    check("未知类型确实抛错", raised)

    print(f"\n通过 {OK} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  ✗", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
