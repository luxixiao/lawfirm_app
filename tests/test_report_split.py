"""报表「按类型拆分」三态（合并 / 仅多类型拆分 / 全拆分）引擎层单元测试 — 内存库。

核心不变量：对任一列表式报表，Σ(拆分各行数值) == 合并行数值（收款/开票/未收/业务收入/费用）。
证明每笔业务唯一归属类型快照 → 严格可加。覆盖：
- 一人多类型（合伙+聘用）开票份额 + 收款 + 费用按类型归因；
- 开票报表 scope（active_types 全部勾线类型）与 聘用 scope（单类型）两种范围内合并；
- 范围内合并排除了非 scope 类型份额（如 聘用 scope 不含 合伙份额）；
- 三态精确行为（含 n==0不出行 / n==1 单类型加括号 / split_multi 单类型不拆）；
- 🔒 锁死「判据是**数据**不是名册配置」：配了 3 类型但当年仅 1 类型有数据 →
  split_multi 不拆（若改用 staff_type.list_person_types 判定，此断言立刻红）；
- normalize_split_mode 的旧 bool / QSettings 'true'/'false' 字符串 / 未知值 / None。

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
    for c, ddl in (("is_invoice", "INTEGER NOT NULL DEFAULT 0"),
                   ("can_expense", "INTEGER NOT NULL DEFAULT 0"),
                   ("net_basis", "TEXT NOT NULL DEFAULT '收款净额'")):
        if c not in cols:
            conn.execute(f"ALTER TABLE staff_type_def ADD COLUMN {c} {ddl}")
    # 复刻 db 迁移：charge_detail / expense_ledger 的 person_type 身份列（不在 SCHEMA 内）
    for tbl in ("charge_detail", "expense_ledger"):
        tcols = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")]
        if "person_type" not in tcols:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN person_type TEXT DEFAULT ''")
    conn.commit()
    return conn


def seed_types(conn) -> None:
    # (name, is_invoice(业务线), can_expense(费用线), net_basis)
    # 映射自旧 is_settle 拆分回填：partner/employee/parttime 业务线开；
    # 原参与结算者（含公共/行政）费用线开。口径按「类型开关」直接设定。
    for name, invoice, expense, basis in (
            ("合伙", True, True, "开票净额"), ("聘用", True, True, "收款净额"),
            ("兼职", True, True, "收款净额"), ("公共", False, True, "收款净额"),
            ("行政", False, True, "收款净额"), ("挂靠", False, False, "收款净额"),
            ("其他", False, False, "收款净额")):
        if st.get_type(name, conn) is None:
            st.add_type(name, conn=conn)
            st.set_invoice(name, invoice, conn=conn)
            st.set_can_expense(name, expense, conn=conn)
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
                 "VALUES(?,?,?,?)", (inv_a, name, 1000, "合伙"))
    conn.execute("INSERT INTO collection(invoice_no, receipt_date, amount, person_name) "
                 "VALUES(?,?,?,?)", (inv_a, "2025-03-10", 1000, name))
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount) VALUES(?,?,?)",
                 (inv_b, "2025-03-01", 400))
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount, person_type) "
                 "VALUES(?,?,?,?)", (inv_b, name, 400, "聘用"))
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
        "VALUES('2025-03','2025-03-05','交通','张三',100,0,100,'交通费','manual','合伙')")
    conn.execute(
        "INSERT INTO expense_ledger(period, exp_date, name, actual_handler, expense_amount, "
        "tax_amount, book_amount, expense_type, source, person_type) "
        "VALUES('2025-03','2025-03-06','交通','张三',50,0,50,'交通费','manual','聘用')")
    conn.commit()


def seed_single_role(conn, name, hire_month=None):
    """李四：名册**只配了合伙**（n==1）。今年有合伙业务 → split_multi 不得拆他。"""
    inv = f"INV_{name}_A"
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount) VALUES(?,?,?)",
                 (inv, "2025-05-01", 700))
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount, person_type) "
                 "VALUES(?,?,?,?)", (inv, name, 700, "合伙"))
    if hire_month:
        conn.execute("INSERT INTO staff_roster(name, hire_month) VALUES(?,?)", (name, hire_month))
    else:
        conn.execute("INSERT INTO staff_roster(name) VALUES(?)", (name,))
    conn.execute("INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) VALUES(?,?,1)",
                 (name, "合伙"))
    conn.commit()


def seed_config_only_extra_type(conn, name):
    """🔒 王五：名册**配了 3 个类型**（合伙+聘用+兼职），但当年**仅合伙有数据**。

    这是 A3 口径的核心反例：n 按**数据**判定= 1，split_multi **不得拆**。
    若实现改用 staff_type.list_person_types(name)（名册配置）判定 n，会误判成 3 → 拆出
    两行 0 值，本文件的断言立刻红。
    """
    inv = f"INV_{name}_A"
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount) VALUES(?,?,?)",
                 (inv, "2025-04-01", 500))
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount, person_type) "
                 "VALUES(?,?,?,?)", (inv, name, 500, "合伙"))
    conn.execute("INSERT INTO staff_roster(name) VALUES(?)", (name,))
    for t, primary in (("合伙", 1), ("聘用", 0), ("兼职", 0)):
        conn.execute("INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) "
                     "VALUES(?,?,?)", (name, t, primary))
    conn.commit()


def _sum(entries, key, *path):
    """对 entries 里每个 st 按 path 取值求和（path 如 ('months',3,'inv_total') 或 ('expenses','交通费',3)）。

    缺键按0 处理（李四/王五没有交通费，expenses 里就没这个键）——求和语义下等价于 0。
    """
    tot = 0.0
    for _, s in entries:
        if s is None:
            continue
        v = s
        for p in path:
            if not isinstance(v, dict):
                v = 0.0
                break
            v = v.get(p, 0.0)
        tot = round(tot + (v or 0.0), 2)
    return tot


def test_normalize_split_mode() -> None:
    """旧 bool / QSettings 字符串 / 未知值 / None 的归一（fail-safe → 合并）。"""
    n = ps.normalize_split_mode
    check("常量值为约定字符串",
          (ps.SPLIT_MERGE, ps.SPLIT_ALL, ps.SPLIT_MULTI) == ("merge", "split_all", "split_multi"),
          f"got={(ps.SPLIT_MERGE, ps.SPLIT_ALL, ps.SPLIT_MULTI)}")
    check("默认= 合并", ps.DEFAULT_SPLIT_MODE == ps.SPLIT_MERGE, ps.DEFAULT_SPLIT_MODE)
    # 旧 bool迁移（旧值不丢，原样恢复用户上一次的选择）
    check("旧 bool True → 全拆分", n(True) == ps.SPLIT_ALL, n(True))
    check("旧 bool False → 合并", n(False) == ps.SPLIT_MERGE, n(False))
    # QSettings 实测坑：setValue(k, True) 读回是字符串 'true'
    check("QSettings 'true' → 全拆分", n("true") == ps.SPLIT_ALL, n("true"))
    check("QSettings 'false' → 合并（不是 True！）", n("false") == ps.SPLIT_MERGE, n("false"))
    check("'True'/'FALSE' 大小写不敏感",
          n("True") == ps.SPLIT_ALL and n("FALSE") == ps.SPLIT_MERGE)
    # 三态字符串原样返回
    for m in (ps.SPLIT_MERGE, ps.SPLIT_ALL, ps.SPLIT_MULTI):
        check(f"三态原样返回：{m}", n(m) == m, n(m))
    # fail-safe：认不出 → 合并（宁合并也不静默出空表）
    check("None → 合并", n(None) == ps.SPLIT_MERGE, n(None))
    check("空串 → 合并", n("") == ps.SPLIT_MERGE, n(""))
    check("空白串 → 合并", n("   ") == ps.SPLIT_MERGE, n("   "))
    check("未知值 → 合并", n("不存在的模式") == ps.SPLIT_MERGE, n("不存在的模式"))
    check("旧两态'split'（月度报表值）→ 合并（不误当全拆分）",
          n("split") == ps.SPLIT_MERGE, n("split"))
    # 标签文案
    check("下拉文案三态齐全",
          (ps.split_mode_label(ps.SPLIT_MERGE), ps.split_mode_label(ps.SPLIT_MULTI),
           ps.split_mode_label(ps.SPLIT_ALL)) == ("合并", "仅多类型拆分", "全拆分（按类型）"))


def test_three_states(conn, types_inv) -> None:
    """三人三态：张三 n=2 / 李四 n=1 / 王五 名册配 3 类型但 n=1。"""
    persons = ["张三", "李四", "王五"]
    merge = ps.report_entries(2025, persons, ps.SPLIT_MERGE, types_inv, conn=conn)
    multi = ps.report_entries(2025, persons, ps.SPLIT_MULTI, types_inv, conn=conn)
    allsp = ps.report_entries(2025, persons, ps.SPLIT_ALL, types_inv, conn=conn)

    d_merge = [d for d, _ in merge]
    d_multi = [d for d, _ in multi]
    d_all = [d for d, _ in allsp]
    check("merge 每人一行（无括号）",
          d_merge == ["张三", "李四", "王五"], d_merge)
    check("split_multi：n>=2 拆、n==1 不拆（王五名册配 3 类型也按数据 n=1 不拆）",
          d_multi == ["张三（合伙）", "张三（聘用）", "李四", "王五"], d_multi)
    check("split_all：全部按类型逐行，单类型也加全角括号",
          d_all == ["张三（合伙）", "张三（聘用）", "李四（合伙）", "王五（合伙）"], d_all)

    # 不变量：三态都满足 Σ 拆分各数值列 == 合并对应列
    for label, entries in (("merge", merge), ("split_multi", multi), ("split_all", allsp)):
        for path in (("months", 3, "inv_total"), ("months", 3, "income"),
                     ("months", 3, "rec_open_cur"), ("uncollected_total",),
                     ("expenses", "交通费", 3)):
            got = _sum(entries, None, *path)
            exp = _sum(merge, None, *path)
            check(f"{label} Σ{'.'.join(map(str, path))}==合并", got == exp,
                  f"{label}={got} merge={exp}")

    # split_all 行的数值 == 合并里该人对应类型的份额（拆出真实值，不是复制合并值）
    m_by = dict(merge)
    check("split_all 张三（合伙）inv_total=1000", allsp[0][1]["months"][3]["inv_total"] == 1000.0,
          allsp[0][1]["months"][3]["inv_total"])
    check("split_all 张三（聘用）inv_total=400", allsp[1][1]["months"][3]["inv_total"] == 400.0,
          allsp[1][1]["months"][3]["inv_total"])
    check("merge 张三 inv_total=1400（Σ 两类型）", m_by["张三"]["months"][3]["inv_total"] == 1400.0,
          m_by["张三"]["months"][3]["inv_total"])
    # split_multi 的李四/王五行 st 必须是**合并**（单类型时两者本就相等）
    mm_by = dict(multi)
    check("split_multi 李四行= 合并行数值",
          mm_by["李四"]["months"][5]["inv_total"] == m_by["李四"]["months"][5]["inv_total"] == 700.0,
          f"{mm_by['李四']['months'][5]['inv_total']}")

    # n==0：merge/split_multi 出一行全 0；split_all 不出行
    empty = ["赵六"]  # 不在 staff_roster 里的纯占位名（引擎无数据）
    e_merge = ps.report_entries(2025, empty, ps.SPLIT_MERGE, types_inv, conn=conn)
    e_multi = ps.report_entries(2025, empty, ps.SPLIT_MULTI, types_inv, conn=conn)
    e_all = ps.report_entries(2025, empty, ps.SPLIT_ALL, types_inv, conn=conn)
    check("n==0：merge 出一行（st=None → 全 0）",
          [d for d, _ in e_merge] == ["赵六"] and e_merge[0][1] is None,
          [d for d, _ in e_merge])
    check("n==0：split_multi 出一行（st=None → 全 0）",
          [d for d, _ in e_multi] == ["赵六"] and e_multi[0][1] is None,
          [d for d, _ in e_multi])
    check("n==0：split_all **不出行**", e_all == [], e_all)


def test_bool_backward_compat(conn, types_inv) -> None:
    """旧 bool 调用点（10 处，含 tests 里的位置/关键字传参）零改动仍走原语义。"""
    b_false = ps.report_entries(2025, ["张三"], False, types_inv, conn=conn)
    b_true = ps.report_entries(2025, ["张三"], True, types_inv, conn=conn)
    check("bool False == SPLIT_MERGE", b_false == ps.report_entries(
        2025, ["张三"], ps.SPLIT_MERGE, types_inv, conn=conn))
    check("bool True == SPLIT_ALL（拆分 + 全角括号）",
          [d for d, _ in b_true] == ["张三（合伙）", "张三（聘用）"],
          [d for d, _ in b_true])
    # 关键字传参（split=）也要能用
    kw = ps.report_entries(2025, ["张三"], split=ps.SPLIT_MULTI, types=types_inv, conn=conn)
    check("关键字传参 split=/types= 可用", [d for d, _ in kw] == ["张三（合伙）", "张三（聘用）"],
          [d for d, _ in kw])


def main() -> int:
    test_normalize_split_mode()

    conn = make_conn()
    seed_types(conn)
    seed_multi_role(conn, "张三")           # n=2（合伙+聘用都有数据）
    seed_single_role(conn, "李四")           # n=1（仅合伙）
    seed_config_only_extra_type(conn, "王五")  # 名册配 3 类型，数据 n=1

    types_inv = st.active_types(conn)        # 全部勾线类型 [(合伙,合伙),...]
    types_staff = [("聘用", "聘用")]

    test_three_states(conn, types_inv)
    test_bool_backward_compat(conn, types_inv)

    # ===== 开票报表 scope（active_types）：三态 + 不变量 =====
    merged = ps.report_entries(2025, ["张三"], False, types_inv, conn=conn)
    split = ps.report_entries(2025, ["张三"], True, types_inv, conn=conn)
    check("开票scope 合并行数=1", len(merged) == 1, f"got={len(merged)}")
    check("开票scope 拆分行数=2（合伙+聘用两类型有数据）", len(split) == 2, f"got={len(split)}")
    check("开票scope 拆分标签=张三（合伙）/张三（聘用）（顺序按类型 sort_order）",
          [d for d, _ in split] == ["张三（合伙）", "张三（聘用）"], [d for d, _ in split])

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

    # ===== 聘用 scope（仅聘用类型）=====
    merged_s = ps.report_entries(2025, ["张三"], False, types_staff, conn=conn)
    split_s = ps.report_entries(2025, ["张三"], True, types_staff, conn=conn)
    check("聘用scope 拆分行数=1（仅聘用，合伙不在 scope）", len(split_s) == 1,
          f"got={len(split_s)}")
    check("聘用scope 拆分标签=张三（聘用）",
          [d for d, _ in split_s] == ["张三（聘用）"], [d for d, _ in split_s])
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
    # 单类型 scope 下 split_multi 不拆（n==1）
    multi_s = ps.report_entries(2025, ["张三"], ps.SPLIT_MULTI, types_staff, conn=conn)
    check("聘用scope split_multi 不拆（n==1）", [d for d, _ in multi_s] == ["张三"],
          [d for d, _ in multi_s])
    check("聘用scope split_multi 行值= 范围内合并",
          _sum(multi_s, None, "months", 3, "inv_total") == 400.0,
          _sum(multi_s, None, "months", 3, "inv_total"))

    conn.close()

    if FAILS:
        print(f"PASS {len(OK)} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
        return 1
    print(f"PASS {len(OK)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
