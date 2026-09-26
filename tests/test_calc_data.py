"""calc_data（DATA() 内置指标取数层）单元测试 — 内存库，不触碰真实数据。

运行：python tests/test_calc_data.py
覆盖：
1. db.SCHEMA 可在内存库完整建表（含 calc_sheet / calc_indicator）
2. 发票/工资/费用类指标手算断言（月 + 全年）
3. 结算类指标与 person_settlement.build_settlement_conn 口径一致性断言
4. 重名职工 → AmbiguousPersonError；未知指标 → UnknownIndicatorError
5. 请求级缓存命中（二次取值不再变化）
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine.calc_data import (  # noqa: E402
    CalcData, AmbiguousPersonError, UnknownIndicatorError, builtin_names,
)
from app.engine import person_settlement as ps  # noqa: E402
from app.engine import staff_type as st  # noqa: E402


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # 复刻 init_db 的关键迁移列（引擎依赖 person_type / received_override / is_settle / net_basis）
    for tbl in ("charge_detail", "expense_ledger"):
        conn.execute(f"ALTER TABLE {tbl} ADD COLUMN person_type TEXT DEFAULT ''")
    conn.execute("ALTER TABLE charge_detail ADD COLUMN received_override REAL DEFAULT NULL")
    # 员工类型参与结算开关（staff_type_def.is_settle / net_basis）—— 复刻 init_db 迁移
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]
    if "is_settle" not in cols:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN is_settle INTEGER NOT NULL DEFAULT 0")
    if "net_basis" not in cols:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN net_basis TEXT NOT NULL DEFAULT '收款净额'")
    # 去写死（Plan A）：角色列 + role_def 种子（复刻 init_db 迁移，幂等）
    if "role_code" not in cols:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN role_code TEXT NOT NULL DEFAULT 'other'")
    st.ensure_roles(conn)
    # 注入员工类型（合伙/聘用/兼职/公共/行政…）：改动 1 后 ensure_defaults 不再预置，
    # 改由 seed_types 显式自建，使结算口径与真实库一致
    seed_types(conn)
    return conn


def seed_types(conn: sqlite3.Connection) -> None:
    """铺出结算口径用到的员工类型（聘用/合伙/兼职 参与）。

    改动 1 后 `ensure_defaults()` 不再预置任何类型（员工类型表初始为空），
    改为由用例显式新建；没有它们结算类指标会全部判 0，口径一致性断言就没意义了。
    """
    for name, settle, basis in (("合伙", True, "开票净额"), ("聘用", True, "收款净额"),
                                ("兼职", True, "收款净额"), ("公共", True, "收款净额"),
                                ("行政", True, "收款净额")):
        if st.get_type(name, conn) is None:
            st.add_type(name, conn=conn)
        st.set_settle(name, settle, conn=conn)
        st.set_net_basis(name, basis, conn=conn)


def seed(conn: sqlite3.Connection) -> None:
    c = conn
    # ---- staff ----
    c.execute("INSERT INTO staff(name, staff_type, is_active) VALUES('周立生','聘用',1)")
    c.execute("INSERT INTO staff(name, staff_type, is_active) VALUES('王合伙','合伙',1)")
    c.execute("INSERT INTO staff(name, staff_type, is_active) VALUES('陈娟','兼职',1)")
    # ---- invoice（正数 + 红字 + 上年票）----
    c.execute("INSERT INTO invoice(invoice_no,invoice_date,total_amount,orig_invoice_no) "
              "VALUES('INV001','2025-03-05',10000,NULL)")
    c.execute("INSERT INTO invoice(invoice_no,invoice_date,total_amount,orig_invoice_no) "
              "VALUES('INV002','2025-05-10',8000,NULL)")
    c.execute("INSERT INTO invoice(invoice_no,invoice_date,total_amount,orig_invoice_no) "
              "VALUES('RED001','2025-06-20',-3000,'INV002')")
    c.execute("INSERT INTO invoice(invoice_no,invoice_date,total_amount,orig_invoice_no) "
              "VALUES('INV003','2024-12-15',5000,NULL)")
    # ---- charge_detail ----
    c.executemany("INSERT INTO charge_detail(invoice_no,person_name,billing_amount,person_type) VALUES(?,?,?,?)", [
        ("INV001", "周立生", 6000, "聘用"),
        ("INV001", "陈娟",   4000, "兼职"),
        ("INV002", "周立生", 8000, "聘用"),
        ("RED001", "周立生", -3000, "聘用"),
        ("INV003", "周立生", 5000, "聘用"),
    ])
    # ---- collection ----
    c.executemany("INSERT INTO collection(invoice_no,receipt_date,amount,person_name) VALUES(?,?,?,?)", [
        ("INV001", "2025-03-10", 6000, "周立生"),
        ("INV001", "2025-03-10", 4000, "陈娟"),
        ("INV002", "2025-07-01", 4000, ""),        # 未归因 → 按份额分摊
        ("INV003", "2025-02-01", 5000, "周立生"),  # ③收上年
    ])
    # ---- refund（RED001 退款 1000 → ④退本年）----
    c.execute("INSERT INTO refund(red_invoice_no,refund_amount,refund_date) "
              "VALUES('RED001',1000,'2025-08-05')")
    # ---- 工资镜像 ----
    c.execute("INSERT INTO import_batch(batch_type,period,file_name,imported_at) "
              "VALUES('ledger','2025-03','t.xls','2025-03-01')")
    c.execute("INSERT INTO import_batch(batch_type,period,file_name,imported_at) "
              "VALUES('ledger','2025-04','t.xls','2025-04-01')")
    c.executemany("INSERT INTO raw_salary(import_batch_id,staff_name,sheet_key,item_type,"
                  "share_num,salary_num,partner_num,tax_num,fund_num,net_num) VALUES(?,?,?,?,?,?,?,?,?,?)", [
        (1, "周立生", "lawyer",   "分成报酬", 5000, 0, 0, 300, 200, 4500),
        (2, "周立生", "lawyer",   "工资",     0, 6000, 0, 400, 250, 5350),
        (1, "王合伙", "partner",  "预发经营所得", 0, 0, 9000, 100, 0, 8900),
    ])
    # ---- 费用 ----
    c.executemany("INSERT INTO expense_ledger(period,actual_handler,expense_type,expense_amount,person_type) VALUES(?,?,?,?,?)", [
        ("2025-03", "周立生", "分成",   800, "聘用"),
        ("2025-07", "周立生", "汽油费", 200, "聘用"),
        ("2025-03", "王合伙", "其他",   100, "合伙"),
    ])
    c.commit()


def near(a: float, b: float, eps: float = 0.01) -> bool:
    return abs(a - b) <= eps


def main() -> int:
    conn = make_conn()
    seed(conn)
    cd = CalcData(conn)
    ok, fails = 0, []

    def check(label, got, want):
        nonlocal ok
        if near(got, want):
            ok += 1
        else:
            fails.append(f"{label}: got={got} want={want}")

    # ===== 1. 发票类（手算）=====
    check("开票金额 3月",  cd.get("周立生", "开票金额", 2025, 3), 6000)
    check("开票金额 5月",  cd.get("周立生", "开票金额", 2025, 5), 8000)
    check("开票金额 全年", cd.get("周立生", "开票金额", 2025), 14000)
    check("开票金额 不含红字票", cd.get("周立生", "开票金额", 2025, 6), 0)
    check("红冲金额 6月",  cd.get("周立生", "红冲金额", 2025, 6), 3000)
    check("红冲金额 全年", cd.get("周立生", "红冲金额", 2025), 3000)

    # ===== 2. 工资 / 费用（手算）=====
    check("每月工资 3月",  cd.get("周立生", "每月工资", 2025, 3), 5000)
    check("每月工资 4月",  cd.get("周立生", "每月工资", 2025, 4), 6000)
    check("每月工资 全年", cd.get("周立生", "每月工资", 2025), 11000)
    check("代扣个税 全年", cd.get("周立生", "代扣个税", 2025), 700)
    check("公积金 全年",   cd.get("周立生", "公积金", 2025), 450)
    check("实发金额 全年", cd.get("周立生", "实发金额", 2025), 9850)
    check("费用合计 3月",  cd.get("周立生", "费用合计", 2025, 3), 800)
    check("费用合计 全年", cd.get("周立生", "费用合计", 2025), 1000)
    check("合伙 每月工资 3月", cd.get("王合伙", "每月工资", 2025, 3), 9000)

    # ===== 3. 结算类（与 person_settlement 口径逐字段一致）=====
    st = ps.build_settlement_conn(conn, 2025, "周立生")["周立生"]
    for m in range(1, 13):
        mm = st["months"][m]
        rec_net = (mm["rec_open_cur"] + mm["rec_cur_year"] + mm["rec_prev_year"]
                   + mm["rec_refund_cur"] + mm["rec_refund_prev"])
        check(f"收款净额 {m}月", cd.get("周立生", "收款净额", 2025, m), rec_net)
        check(f"业务收入 {m}月", cd.get("周立生", "业务收入", 2025, m), mm["income"])
        check(f"开票净额 {m}月", cd.get("周立生", "开票净额", 2025, m), mm["inv_total"])
        check(f"未收款 {m}月",   cd.get("周立生", "未收款金额", 2025, m),
              st["uncollected_month"][m])
        red_abs = abs(mm["inv_red_cur"] + mm["inv_red_prev"])
        check(f"红冲(结算) {m}月", cd.get("周立生", "红冲金额", 2025, m), red_abs)
        refund_abs = abs(mm["rec_refund_cur"] + mm["rec_refund_prev"])
        check(f"退款 {m}月", cd.get("周立生", "退款金额", 2025, m), refund_abs)
    check("未收款 全年(存量累计)", cd.get("周立生", "未收款金额", 2025),
          st["uncollected_total"])

    # 口径抽检：③收上年 → 2月收款净额含上年票收款 5000
    check("③收上年 2月=5000", cd.get("周立生", "收款净额", 2025, 2), 5000)
    # ④退本年 → 8月退款金额 1000
    check("④退本年 8月=1000", cd.get("周立生", "退款金额", 2025, 8), 1000)

    # ===== 4. 别名 / 异常 / 缓存 =====
    check("别名 已收净额=收款净额", cd.get("周立生", "已收净额", 2025, 3),
          cd.get("周立生", "收款净额", 2025, 3))
    try:
        # 重名场景：staff.name 有 UNIQUE 约束，正常数据不会重名；
        # 重建无约束表模拟脏数据，验证 CalcData 的防御逻辑
        conn.executescript(
            "CREATE TABLE staff_dup AS SELECT * FROM staff;"
            "INSERT INTO staff_dup(name, staff_type, is_active) VALUES('陈娟','聘用',1);"
            "DROP TABLE staff;"
            "ALTER TABLE staff_dup RENAME TO staff;")
        cd2 = CalcData(conn)
        cd2.get("陈娟", "开票金额", 2025, 3)
        fails.append("重名未报错")
    except AmbiguousPersonError:
        ok += 1
    try:
        cd.get("周立生", "不存在的指标", 2025, 3)
        fails.append("未知指标未报错")
    except UnknownIndicatorError:
        ok += 1
    check("缓存命中一致", cd.get("周立生", "开票金额", 2025, 3),
          cd.get("周立生", "开票金额", 2025, 3))
    check("离职/无数据人 = 0", cd.get("张无名", "开票金额", 2025, 3), 0)

    # ===== 5. calc_sheet / calc_indicator 表存在 =====
    for tbl in ("calc_sheet", "calc_indicator"):
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {tbl}").fetchone()["n"]
        if n == 0:
            ok += 1
        else:
            fails.append(f"{tbl} 应为空表")
    if len(builtin_names()) >= 14:
        ok += 1
    else:
        fails.append(f"内置指标数量异常: {len(builtin_names())}")

    conn.close()
    print(f"PASS {ok} checks" if not fails else "FAILED:\n" + "\n".join(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
