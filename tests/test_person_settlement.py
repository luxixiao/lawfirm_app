"""person_settlement 业务收入口径重构（feature 5 + B）单元测试 — 内存库。

G 核心：迁移/默认值保证「合伙=开票净额、聘用/兼职=收款净额、其余=0」与旧硬编码
逐字节一致；自定义类型开启参与后可按所选净额口径计收入。

运行：python tests/test_person_settlement.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import staff_type as st  # noqa: E402
from app.engine import person_settlement as ps  # noqa: E402

OK, FAILS = 0, []


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # 与 db.init_db 迁移块一致：staff 入职月份 + 员工类型参与结算开关（幂等）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff)")]
    if "hire_month" not in cols:
        conn.execute("ALTER TABLE staff ADD COLUMN hire_month TEXT DEFAULT ''")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]
    if "is_settle" not in cols:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN is_settle INTEGER NOT NULL DEFAULT 0")
    if "net_basis" not in cols:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN net_basis TEXT NOT NULL DEFAULT '收款净额'")
    conn.commit()
    return conn


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def seed_person(conn, name, staff_type, billing, collected):
    """为某人造一张本年发票（billing）+ 收款（collected），便于断言收入口径。

    完全收款 → rec_net == inv_total；部分收款 → rec_net < inv_total，可区分净额口径。
    """
    inv = "INV_" + name
    conn.execute(
        "INSERT INTO invoice(invoice_no, invoice_date, total_amount) VALUES(?,?,?)",
        (inv, "2025-03-01", billing))
    conn.execute(
        "INSERT INTO charge_detail(invoice_no, person_name, billing_amount) VALUES(?,?,?)",
        (inv, name, billing))
    if collected:
        conn.execute(
            "INSERT INTO collection(invoice_no, receipt_date, amount, person_name) VALUES(?,?,?,?)",
            (inv, "2025-03-10", collected, name))
    conn.execute("INSERT INTO staff(name, staff_type) VALUES(?,?)", (name, staff_type))
    conn.commit()


def seed_red_scenario(conn, name):
    """红冲例（用户确认口径）：1月开 a 1000 未收；2月红冲 a -1000（原票同年1月）。
    期望：全年累计未收 = 0（红冲抵消）；仅统计到1月时累计 = 1000。
    """
    inv = "INV_RED_" + name
    red = "RED_RED_" + name
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount) VALUES(?,?,?)",
                 (inv, "2025-01-15", 1000))
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount) VALUES(?,?,?)",
                 (inv, name, 1000))
    # 红字发票：total_amount<0，orig_invoice_no 指原票
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount, orig_invoice_no) "
                 "VALUES(?,?,?,?)", (red, "2025-02-15", -1000, inv))
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount) VALUES(?,?,?)",
                 (red, name, -1000))
    conn.execute("INSERT INTO staff(name, staff_type) VALUES(?,?)", (name, "合伙"))
    conn.commit()


def seed_refund_scenario(conn, name):
    """退款例（用户确认口径）：1月开 a 1000 未收；2月红冲 a -1000；3月退款 1000（随红票）。
    逐月累计：1月=1000（蓝字未收）；2月=0（红冲⑧抵消）；3月=1000（退款④使现金流出→累计回升）；
    全年累计=1000（红冲与退款跨月，不在同一月抵消）。演示 ④⑤ 进入累计未收。
    注：系统退款必关联红字发票（refund.red_invoice_no → invoice），无红字直接退款在系统中被忽略。
    """
    inv = "INV_REF_" + name
    red = "RED_REF_" + name
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount) VALUES(?,?,?)",
                 (inv, "2025-01-15", 1000))
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount) VALUES(?,?,?)",
                 (inv, name, 1000))
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount, orig_invoice_no) "
                 "VALUES(?,?,?,?)", (red, "2025-02-15", -1000, inv))
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount) VALUES(?,?,?)",
                 (red, name, -1000))
    # 退款（退本年④）：refund_date 在 3月
    conn.execute("INSERT INTO refund(red_invoice_no, refund_amount, refund_date) VALUES(?,?,?)",
                 (red, 1000, "2025-03-05"))
    conn.execute("INSERT INTO staff(name, staff_type) VALUES(?,?)", (name, "合伙"))
    conn.commit()


def main() -> int:
    conn = make_conn()
    st.ensure_defaults(conn)

    # ===== 1. settle_flags_of（G 核心驱动）=====
    check("合伙=(1,开票净额)", ps.staff_type.settle_flags_of("合伙", conn) == (True, "开票净额"))
    check("聘用=(1,收款净额)", ps.staff_type.settle_flags_of("聘用", conn) == (True, "收款净额"))
    check("兼职=(1,收款净额)", ps.staff_type.settle_flags_of("兼职", conn) == (True, "收款净额"))
    check("挂靠=(0,收款净额)", ps.staff_type.settle_flags_of("挂靠", conn) == (False, "收款净额"))
    check("其他=(0,收款净额)", ps.staff_type.settle_flags_of("其他", conn) == (False, "收款净额"))
    check("公共=(1,收款净额)", ps.staff_type.settle_flags_of("公共", conn) == (True, "收款净额"))
    check("行政=(1,收款净额)", ps.staff_type.settle_flags_of("行政", conn) == (True, "收款净额"))

    # 自定义类型开启参与 + 选开票净额
    st.add_type("自定义甲", conn=conn)
    st.set_settle("自定义甲", True, conn=conn)
    st.set_net_basis("自定义甲", "开票净额", conn=conn)
    check("自定义甲=(1,开票净额)", ps.staff_type.settle_flags_of("自定义甲", conn) == (True, "开票净额"))

    # ===== 2. build_settlement 集成（逐字节一致 G 核心）=====
    # 合伙：无收款 → rec_net=0，income 必须=inv_total(1000)（开票净额）
    seed_person(conn, "王合伙", "合伙", 1000, 0)
    # 聘用：部分收款(700) → rec_net=700，income 必须=700（收款净额）<> inv_total(1000)
    seed_person(conn, "李聘用", "聘用", 1000, 700)
    # 兼职：部分收款(300) → rec_net=300，income 必须=300（收款净额）
    seed_person(conn, "张兼职", "兼职", 500, 300)

    res = ps.build_settlement_conn(conn, 2025)
    check("合伙 收入=inv_total(开票净额)",
          res["王合伙"]["months"][3]["income"] == 1000.0, f"got={res['王合伙']['months'][3]['income']}")
    check("聘用 收入=rec_net(收款净额)",
          res["李聘用"]["months"][3]["income"] == 700.0, f"got={res['李聘用']['months'][3]['income']}")
    check("兼职 收入=rec_net(收款净额)",
          res["张兼职"]["months"][3]["income"] == 300.0, f"got={res['张兼职']['months'][3]['income']}")
    # 其他月无业务 → 收入 0
    check("合伙 其他月收入=0", res["王合伙"]["months"][1]["income"] == 0.0)

    # ===== 3. 自定义类型开启参与后按所选净额口径计收入 =====
    # 注：当前结算引擎把非关键字 staff_type 归一到 "其他"，故对自定义名设置参与即作用于
    # 该归一类；这里直接对 "其他" 开启参与并选开票净额，验证自定义（非内置）类型参与结算。
    st.set_settle("其他", True, conn=conn)
    st.set_net_basis("其他", "开票净额", conn=conn)
    seed_person(conn, "赵自由", "自由职业", 800, 0)  # 自定义名 → 归一到 其他
    res2 = ps.build_settlement_conn(conn, 2025)
    check("自定义(归其他) 开启参与+开票净额 收入=inv_total",
          res2["赵自由"]["months"][3]["income"] == 800.0,
          f"got={res2['赵自由']['months'][3]['income']}")

    # ===== 4. P0-2 回归：累计未收须含红冲/退款冲减（口径=Σ(三−收款净额)）=====
    # 红冲例：1月开1000未收，2月红冲-1000（原票同年）
    seed_red_scenario(conn, "钱红冲")
    res_r = ps.build_settlement_conn(conn, 2025)
    sr = res_r["钱红冲"]
    check("P0-2 红冲 全年累计未收=0", sr["uncollected_total"] == 0.0, f"got={sr['uncollected_total']}")
    check("P0-2 红冲 仅1月累计=1000", ps.cumulative_uncollected(sr, 1) == 1000.0,
          f"got={ps.cumulative_uncollected(sr, 1)}")
    check("P0-2 红冲 2月累计=0", ps.cumulative_uncollected(sr, 2) == 0.0,
          f"got={ps.cumulative_uncollected(sr, 2)}")
    # 红冲月(2月) 三小计含⑧=-1000、收款净额=0 → 该月差额=-1000，已冲减累计
    check("P0-2 红冲 2月 inv_red_cur=-1000", sr["months"][2]["inv_red_cur"] == -1000.0,
          f"got={sr['months'][2]['inv_red_cur']}")

    # 退款例：1月开1000未收，2月红冲-1000，3月退款1000（④）
    seed_refund_scenario(conn, "孙退款")
    res_f = ps.build_settlement_conn(conn, 2025)
    sf = res_f["孙退款"]
    check("P0-2 退款 1月累计=1000", ps.cumulative_uncollected(sf, 1) == 1000.0,
          f"got={ps.cumulative_uncollected(sf, 1)}")
    check("P0-2 退款 2月累计=0（红冲抵消）", ps.cumulative_uncollected(sf, 2) == 0.0,
          f"got={ps.cumulative_uncollected(sf, 2)}")
    check("P0-2 退款 3月累计=1000（退款④回升）", ps.cumulative_uncollected(sf, 3) == 1000.0,
          f"got={ps.cumulative_uncollected(sf, 3)}")
    check("P0-2 退款 3月 rec_refund_cur=-1000", sf["months"][3]["rec_refund_cur"] == -1000.0,
          f"got={sf['months'][3]['rec_refund_cur']}")
    check("P0-2 退款 全年累计=1000", sf["uncollected_total"] == 1000.0,
          f"got={sf['uncollected_total']}")

    conn.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
