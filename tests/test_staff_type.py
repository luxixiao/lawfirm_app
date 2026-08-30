"""staff_type（员工类型维护 + 员工删除引用检查）单元测试 — 内存库。

运行：python tests/test_staff_type.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import staff_type as st  # noqa: E402
from app.engine.staff_type import StaffInUseError, StaffTypeError  # noqa: E402

OK, FAILS = 0, []


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("ALTER TABLE staff ADD COLUMN hire_month TEXT DEFAULT ''")   # init_db 迁移列
    conn.commit()
    return conn


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def expect_err(label, fn, exc=StaffTypeError):
    global OK
    try:
        fn()
        FAILS.append(f"{label}: 未抛出 {exc.__name__}")
    except exc:
        OK += 1


def main() -> int:
    conn = make_conn()

    # ===== 1. 内置类型初始化 =====
    st.ensure_defaults(conn)
    names = [t["name"] for t in st.list_types(conn)]
    check("预置 5 类", names == ["合伙", "聘用", "兼职", "挂靠", "其他"], f"got={names}")
    builtin = {t["name"]: t["is_builtin"] for t in st.list_types(conn)}
    check("合伙是内置", builtin["合伙"] == 1)
    check("兼职是内置", builtin["兼职"] == 1)
    check("挂靠非内置", builtin["挂靠"] == 0)

    # ===== 2. 参与结算口径 =====
    check("合伙参与计算", st.is_computable("合伙") is True)
    check("聘用参与计算", st.is_computable("聘用") is True)
    check("兼职参与计算", st.is_computable("兼职") is True)
    check("顾问不参与", st.is_computable("顾问") is False)
    check("其他不参与", st.is_computable("其他") is False)
    check("含'合伙'字样的自定义名参与", st.is_computable("外部合伙") is True)

    # ===== 3. 新增 / 改名 / 说明 / 排序 =====
    st.add_type("顾问", "外部顾问律师", conn=conn)
    check("新增后 6 类", len(st.list_types(conn)) == 6)
    expect_err("重名拒绝", lambda: st.add_type("顾问", conn=conn))
    expect_err("空名拒绝", lambda: st.add_type("  ", conn=conn))

    st.rename_type("顾问", "外部专家", conn=conn)
    check("改名生效", "外部专家" in [t["name"] for t in st.list_types(conn)])
    expect_err("内置禁改名", lambda: st.rename_type("合伙", "合伙人", conn=conn))

    st.set_note("合伙", "按开票净额计业务收入", conn=conn)
    check("内置可改说明", st.get_type("合伙", conn)["note"] == "按开票净额计业务收入")

    st.add_type("实习", conn=conn)
    order1 = [t["name"] for t in st.list_types(conn)]
    st.move_type("实习", -1, conn=conn)
    order2 = [t["name"] for t in st.list_types(conn)]
    check("上移改变顺序", order1 != order2 and order2.index("实习") < order1.index("实习"),
          f"{order1} -> {order2}")

    # ===== 4. 删除类型：内置禁删 / 有人在用禁删 =====
    expect_err("内置禁删", lambda: st.delete_type("聘用", conn=conn))
    conn.execute("INSERT INTO staff(name, staff_type) VALUES('张三','顾问')")
    conn.commit()
    st.add_type("顾问", conn=conn)   # 重新加回（前面改名成了外部专家）
    expect_err("有员工在用禁删", lambda: st.delete_type("顾问", conn=conn))
    # 员工改类型后可删
    conn.execute("UPDATE staff SET staff_type='聘用' WHERE name='张三'")
    conn.commit()
    st.delete_type("顾问", conn=conn)
    check("无人使用可删", "顾问" not in [t["name"] for t in st.list_types(conn)])

    # ===== 5. 引用人数统计 =====
    types = {t["name"]: t["staff_count"] for t in st.list_types(conn)}
    check("聘用人数=1", types["聘用"] == 1, f"got={types}")

    # ===== 6. 导入补齐未知类型 =====
    st.ensure_types(conn, ["顾问", "返聘"])
    check("导入自动补类型", "返聘" in [t["name"] for t in st.list_types(conn)])

    # ===== 7. 员工引用检查与删除 =====
    refs = st.staff_reference_count("张三", conn)
    check("无引用 total=0", refs["total"] == 0, f"got={refs}")
    st.delete_staff("张三", conn)
    check("无引用可删除", conn.execute(
        "SELECT COUNT(*) AS n FROM staff WHERE name='张三'").fetchone()["n"] == 0)

    # 造引用：发票经办人 + 收款 + 费用 + 工资
    conn.execute("INSERT INTO staff(name, staff_type) VALUES('李四','合伙')")
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount) "
                 "VALUES('INV1','2025-03-01',1000)")
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount) "
                 "VALUES('INV1','李四',1000)")
    conn.execute("INSERT INTO collection(invoice_no, receipt_date, amount) "
                 "VALUES('INV1','2025-03-05',1000)")
    conn.execute("INSERT INTO expense_ledger(period, actual_handler, expense_amount) "
                 "VALUES('2025-03','李四',100)")
    conn.commit()
    refs = st.staff_reference_count("李四", conn)
    check("charge_detail 1", refs["charge_detail"] == 1, f"got={refs}")
    check("collection 1", refs["collection"] == 1, f"got={refs}")
    check("expense_ledger 1", refs["expense_ledger"] == 1, f"got={refs}")
    check("合计 3", refs["total"] == 3, f"got={refs}")
    try:
        st.delete_staff("李四", conn)
        FAILS.append("有引用仍被删除")
    except StaffInUseError as e:
        check("有引用拒绝删除并提示停用", "停用" in str(e), f"msg={e}")
    check("拒绝后员工仍在", conn.execute(
        "SELECT COUNT(*) AS n FROM staff WHERE name='李四'").fetchone()["n"] == 1)

    conn.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
