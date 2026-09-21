"""费用台账详情页保存内核单元测试（app.engine.expense_edit.apply_expense_edits）— 内存库。

运行：python tests/test_expense_edit.py

覆盖：单字段改 / 金额+税额改→账面金额自动重算并落 change_log / 无变动行跳过 /
多行批量 / 字段级 diff 写 change_log（edit 标记 + 各字段新旧值）。
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import expense_edit as ee  # noqa: E402

OK, FAILS = 0, []


def check(cond, msg):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(msg)


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# 注：基础 SCHEMA 不含 person_type（由迁移 ALTER 加入），此处用基础 schema 播种，
# 不写 person_type 列；diff 逻辑对「缺失且未改动」的字段安全跳过。
_SEED = [
    (1, "2025-01", 1, "2025-01-05", "打车费", "T1", "张三", "张三",
     100.0, 6.0, 94.0, "差旅", "V001", "交通费", "市内交通", "import", 10),
    (2, "2025-01", 2, "2025-01-06", "餐费", "T2", "李四", "李四",
     200.0, 12.0, 188.0, "招待", "V002", "业务费", "餐饮", "import", 10),
]


def seed(conn):
    conn.executemany(
        "INSERT INTO expense_ledger "
        "(id, period, seq, exp_date, name, ticket_no, handler, actual_handler, "
        " expense_amount, tax_amount, book_amount, expense_type, voucher_no, "
        " subject1, subject2, source, import_batch_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _SEED)
    conn.commit()


def row_by_id(conn, rid):
    return dict(conn.execute(
        "SELECT * FROM expense_ledger WHERE id=?", (rid,)).fetchone())


def log_fields(conn, rid):
    return {r["field"] for r in conn.execute(
        "SELECT field FROM change_log WHERE table_name='expense_ledger' "
        "AND record_id=?", (str(rid),)).fetchall()}


def test_single_field():
    conn = make_conn()
    seed(conn)
    orig = row_by_id(conn, 1)
    new = {k: orig.get(k) for k in ee.EXPENSE_EDITABLE_KEYS}
    new["name"] = "打车费（改）"
    new["book_amount"] = round(float(new["expense_amount"]) - float(new["tax_amount"]), 2)
    n = ee.apply_expense_edits(conn, [(1, orig, new)])
    conn.commit()
    check(n == 1, "T1 实际更新行数应为 1")
    r = row_by_id(conn, 1)
    check(r["name"] == "打车费（改）", "T1 名称已更新")
    f = log_fields(conn, 1)
    check("name" in f, "T1 change_log 含 name 字段")
    check("edit" in f, "T1 change_log 含 edit 标记")
    check("book_amount" not in f, "T1 未改金额，不应记录 book_amount")


def test_amount_tax_recompute():
    conn = make_conn()
    seed(conn)
    orig = row_by_id(conn, 2)
    new = {k: orig.get(k) for k in ee.EXPENSE_EDITABLE_KEYS}
    new["expense_amount"] = 300.0
    new["tax_amount"] = 18.0
    new["book_amount"] = round(300.0 - 18.0, 2)  # 282.0
    n = ee.apply_expense_edits(conn, [(2, orig, new)])
    conn.commit()
    r = row_by_id(conn, 2)
    check(abs(r["expense_amount"] - 300.0) < 1e-9, "T2 费用金额已更新")
    check(abs(r["book_amount"] - 282.0) < 1e-9, "T2 账面金额随金额/税额自动重算")
    f = log_fields(conn, 2)
    check("expense_amount" in f and "tax_amount" in f and "book_amount" in f,
          "T2 change_log 含 expense_amount/tax_amount/book_amount")


def test_noop_skipped():
    conn = make_conn()
    seed(conn)
    orig = row_by_id(conn, 1)
    new = {k: orig.get(k) for k in ee.EXPENSE_EDITABLE_KEYS}
    new["book_amount"] = round(float(orig["expense_amount"]) - float(orig["tax_amount"]), 2)
    n = ee.apply_expense_edits(conn, [(1, orig, new)])
    conn.commit()
    check(n == 0, "T3 无变动行应跳过（返回 0）")
    check(conn.execute("SELECT COUNT(*) FROM change_log WHERE record_id='1'").fetchone()[0] == 0,
          "T3 不应写 change_log")


def test_multi_row():
    conn = make_conn()
    seed(conn)
    o1 = row_by_id(conn, 1)
    o2 = row_by_id(conn, 2)
    n1 = {k: o1.get(k) for k in ee.EXPENSE_EDITABLE_KEYS}
    n1["name"] = "A"
    n1["book_amount"] = round(float(n1["expense_amount"]) - float(n1["tax_amount"]), 2)
    n2 = {k: o2.get(k) for k in ee.EXPENSE_EDITABLE_KEYS}
    n2["expense_type"] = "B"
    n2["book_amount"] = round(float(n2["expense_amount"]) - float(n2["tax_amount"]), 2)
    n = ee.apply_expense_edits(conn, [(1, o1, n1), (2, o2, n2)])
    conn.commit()
    check(n == 2, "T4 两行均更新")
    check(row_by_id(conn, 1)["name"] == "A", "T4 行1 名称更新")
    check(row_by_id(conn, 2)["expense_type"] == "B", "T4 行2 类型更新")


if __name__ == "__main__":
    test_single_field()
    test_amount_tax_recompute()
    test_noop_skipped()
    test_multi_row()
    print(f"通过 {OK} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  FAIL:", f)
