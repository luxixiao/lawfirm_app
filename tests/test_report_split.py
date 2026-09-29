"""合并/拆分开关（报表加合并/拆分）引擎层单元测试 — 内存库。

核心不变量：对任一列表式报表，Σ(拆分各行数值) == 合并行数值（收款/开票/未收/业务收入/费用）。
证明每笔业务唯一归属角色码 → 严格可加。覆盖：
- 一人多角色（合伙+聘用）开票份额 + 收款 + 费用按角色归因；
- 开票报表 scope（identity_roles）与 聘用报表 scope（employee/parttime）两种范围内合并；
- 范围内合并排除了非 scope 角色份额（如 聘用报表不含合伙份额）。

运行：python tests/test_report_split.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import staff_type as st  # noqa: E402
from app.engine import person_settlement as ps  # noqa: E402

OK, FAILS = [], []


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]
    for c, ddl in (("is_settle", "INTEGER NOT NULL DEFAULT 0"),
                   ("net_basis", "TEXT NOT NULL DEFAULT '收款净额'"),
                   ("role_code", "TEXT NOT NULL DEFAULT 'other'")):
        if c not in cols:
            conn.execute(f"ALTER TABLE staff_type_def ADD COLUMN {c} {ddl}")
    # 复刻 db 迁移：charge_detail / expense_ledger 的 person_type 身份列（不在 SCHEMA 内）
    for tbl in ("charge_detail", "expense_ledger"):
        tcols = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")]
        if "person_type" not in tcols:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN person_type TEXT DEFAULT ''")
    st.ensure_roles(conn)
    conn.commit()
    return conn


def seed_types(conn) -> None:
    for name, settle, basis in (("合伙", True, "开票净额"), ("聘用", True, "收款净额"),
                                ("兼职", True, "收款净额"), ("公共", True, "收款净额"),
                                ("行政", True, "收款净额"), ("挂靠", False, "收款净额"),
                                ("其他", False, "收款净额")):
        if st.get_type(name, conn) is None:
            st.add_type(name, conn=conn)
            st.set_settle(name, settle, conn=conn)
            st.set_net_basis(name, basis, conn=conn)


def check(label, cond, detail=""):
    if cond:
        OK.append(label)
    else:
        FAILS.append(f"{label} {detail}".strip())


def seed_multi_role(conn, name):
    """张三：合伙(primary)+聘用 两类型；两张发票分别按不同角色归因（A票合伙1000 / B票聘用400，全收）；
    费用 交通费 按角色：合伙100 / 聘用50。
    注：charge_detail 唯一键为 (invoice_no, person_name)，同票每人仅一行一个角色，
    故「一人多角色」须跨发票（A票记合伙、B票记聘用）体现。"""
    inv_a, inv_b = "INV_ZS_A", "INV_ZS_B"
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount) VALUES(?,?,?)",
                 (inv_a, "2025-03-01", 1000))
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount, person_type) "
                 "VALUES(?,?,?,?)", (inv_a, name, 1000, "partner"))
    conn.execute("INSERT INTO collection(invoice_no, receipt_date, amount, person_name) "
                 "VALUES(?,?,?,?)", (inv_a, "2025-03-10", 1000, name))
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount) VALUES(?,?,?)",
                 (inv_b, "2025-03-01", 400))
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount, person_type) "
                 "VALUES(?,?,?,?)", (inv_b, name, 400, "employee"))
    conn.execute("INSERT INTO collection(invoice_no, receipt_date, amount, person_name) "
                 "VALUES(?,?,?,?)", (inv_b, "2025-03-10", 400, name))
    conn.execute("INSERT OR IGNORE INTO staff_roster(name) VALUES(?)", (name,))
    conn.execute("INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) VALUES(?,?,1)",
                 (name, "合伙"))
    conn.execute("INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) VALUES(?,?,0)",
                 (name, "聘用"))
    conn.execute(
        "INSERT INTO expense_ledger(period, exp_date, name, actual_handler, expense_amount, "
        "tax_amount, book_amount, expense_type, source, person_type) "
        "VALUES('2025-03','2025-03-05','交通','张三',100,0,100,'交通费','manual','partner')")
    conn.execute(
        "INSERT INTO expense_ledger(period, exp_date, name, actual_handler, expense_amount, "
        "tax_amount, book_amount, expense_type, source, person_type) "
        "VALUES('2025-03','2025-03-06','交通','张三',50,0,50,'交通费','manual','employee')")
    conn.commit()


def _sum(entries, key, *path):
    """对 entries 里每个 st 按 path 取值求和（path 如 ('months',3,'inv_total') 或 ('expenses','交通费',3)）。"""
    tot = 0.0
    for _, s in entries:
        if s is None:
            continue
        v = s
        for p in path:
            v = v[p]
        tot = round(tot + v, 2)
    return tot


def main() -> int:
    conn = make_conn()
    seed_types(conn)
    seed_multi_role(conn, "张三")

    roles_inv = st.identity_roles(conn)            # [(partner,合伙),(employee,聘用),(parttime,兼职)]
    roles_staff = [("employee", "聘用"), ("parttime", "兼职")]

    # ===== 开票报表 scope（identity_roles）=====
    merged = ps.report_entries(2025, ["张三"], False, roles_inv, conn=conn)
    split = ps.report_entries(2025, ["张三"], True, roles_inv, conn=conn)
    check("开票scope 合并行数=1", len(merged) == 1, f"got={len(merged)}")
    check("开票scope 拆分行数=2（合伙+聘用）", len(split) == 2, f"got={len(split)}")
    check("开票scope 拆分标签=张三合伙/张三聘用（顺序按 role_code）",
          set(d for d, _ in split) == {"张三合伙", "张三聘用"}, f"got={[d for d, _ in split]}")

    # Σ拆分 == 合并（全部数值列）
    check("开票scope Σinv_total==合并",
          _sum(split, None, "months", 3, "inv_total") == _sum(merged, None, "months", 3, "inv_total"),
          f"split={_sum(split, None, 'months', 3, 'inv_total')} merged={_sum(merged, None, 'months', 3, 'inv_total')}")
    check("开票scope Σincome==合并",
          _sum(split, None, "months", 3, "income") == _sum(merged, None, "months", 3, "income"),
          f"split={_sum(split, None, 'months', 3, 'income')} merged={_sum(merged, None, 'months', 3, 'income')}")
    check("开票scope Σuncollected_total==合并",
          _sum(split, None, "uncollected_total") == _sum(merged, None, "uncollected_total"),
          f"split={_sum(split, None, 'uncollected_total')} merged={_sum(merged, None, 'uncollected_total')}")
    check("开票scope Σ费用(交通费,3月)==合并",
          _sum(split, None, "expenses", "交通费", 3) == _sum(merged, None, "expenses", "交通费", 3),
          f"split={_sum(split, None, 'expenses', '交通费', 3)} merged={_sum(merged, None, 'expenses', '交通费', 3)}")
    # 合并值应为全范围（合伙1000+聘用400=1400）
    check("开票scope 合并 inv_total=1400", _sum(merged, None, "months", 3, "inv_total") == 1400.0,
          f"got={_sum(merged, None, 'months', 3, 'inv_total')}")

    # ===== 聘用报表 scope（employee/parttime）=====
    merged_s = ps.report_entries(2025, ["张三"], False, roles_staff, conn=conn)
    split_s = ps.report_entries(2025, ["张三"], True, roles_staff, conn=conn)
    check("聘用scope 拆分行数=1（仅聘用，合伙不在 scope）", len(split_s) == 1,
          f"got={len(split_s)}")
    check("聘用scope 拆分标签=张三聘用",
          [d for d, _ in split_s] == ["张三聘用"], f"got={[d for d, _ in split_s]}")
    # 范围内合并排除合伙份额 → 合并=聘用份额=400
    check("聘用scope Σ拆分==范围内合并",
          _sum(split_s, None, "months", 3, "inv_total") == _sum(merged_s, None, "months", 3, "inv_total"),
          f"split={_sum(split_s, None, 'months', 3, 'inv_total')} merged={_sum(merged_s, None, 'months', 3, 'inv_total')}")
    check("聘用scope 合并 inv_total=400（不含合伙600）",
          _sum(merged_s, None, "months", 3, "inv_total") == 400.0,
          f"got={_sum(merged_s, None, 'months', 3, 'inv_total')}")
    check("聘用scope 合并 费用(交通费)=50（不含合伙100）",
          _sum(merged_s, None, "expenses", "交通费", 3) == 50.0,
          f"got={_sum(merged_s, None, 'expenses', '交通费', 3)}")

    conn.close()
    print(f"PASS {len(OK)} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
