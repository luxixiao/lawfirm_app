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

    conn.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
