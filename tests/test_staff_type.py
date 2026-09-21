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
    # 员工类型参与结算开关迁移（与 db.init_db 迁移块一致，幂等）
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
    check("预置 7 类(含公共/行政)", names == ["合伙", "聘用", "兼职", "公共", "行政", "挂靠", "其他"],
          f"got={names}")
    builtin = {t["name"]: t["is_builtin"] for t in st.list_types(conn)}
    check("合伙是内置", builtin["合伙"] == 1)
    check("兼职是内置", builtin["兼职"] == 1)
    check("公共是内置", builtin["公共"] == 1)
    check("行政是内置", builtin["行政"] == 1)
    check("挂靠非内置", builtin["挂靠"] == 0)
    # 内置三类参与结算且净额口径正确（迁移/默认值保证）
    check("合伙 is_settle=1 且 开票净额",
          st.get_type("合伙", conn)["is_settle"] == 1 and st.get_type("合伙", conn)["net_basis"] == "开票净额")
    check("公共 is_settle=1", st.get_type("公共", conn)["is_settle"] == 1)
    # list_types 现在透出 is_settle / net_basis（T5 UI 直接读，不再派生）
    lt = {t["name"]: t for t in st.list_types(conn)}
    check("list_types 透出 is_settle", "is_settle" in lt["合伙"])
    check("list_types 透出 net_basis", "net_basis" in lt["合伙"])
    check("list_types 合伙 is_settle=1", lt["合伙"]["is_settle"] == 1)

    # ===== 2. 参与结算口径（is_computable 改为读 is_settle）=====
    # 注：单参 is_computable(name) 仅用于生产（staff_view.py:175），其内部走真实库；
    # 此处内存库测试须显式传 conn，避免落到未迁移的真实库文件。
    check("合伙参与计算", st.is_computable("合伙", conn) is True)
    check("聘用参与计算", st.is_computable("聘用", conn) is True)
    check("兼职参与计算", st.is_computable("兼职", conn) is True)
    check("顾问不参与", st.is_computable("顾问", conn) is False)
    check("其他不参与", st.is_computable("其他", conn) is False)
    # 旧启发式（含"合伙"字样即参与）已退休：自定义名未开启参与结算 → False
    check("含'合伙'字样的自定义名不参与（未开启）", st.is_computable("外部合伙", conn) is False)

    # ===== 2.1 settle_flags_of / is_settle_participant =====
    check("合伙 settle=(1,开票净额)", st.settle_flags_of("合伙", conn) == (True, "开票净额"))
    check("聘用 settle=(1,收款净额)", st.settle_flags_of("聘用", conn) == (True, "收款净额"))
    check("兼职 settle=(1,收款净额)", st.settle_flags_of("兼职", conn) == (True, "收款净额"))
    check("公共 settle=(1,收款净额)", st.settle_flags_of("公共", conn) == (True, "收款净额"))
    check("行政 settle=(1,收款净额)", st.settle_flags_of("行政", conn) == (True, "收款净额"))
    check("挂靠 settle=(0,收款净额)", st.settle_flags_of("挂靠", conn) == (False, "收款净额"))
    check("其他 settle=(0,收款净额)", st.settle_flags_of("其他", conn) == (False, "收款净额"))
    check("未知类型 settle=(0,收款净额)", st.settle_flags_of("不存在的类型", conn) == (False, "收款净额"))

    check("空名非参与", st.is_settle_participant("", conn) is False)
    check("空名(None)非参与", st.is_settle_participant(None, conn) is False)
    check("合伙参与", st.is_settle_participant("合伙", conn) is True)
    check("公共参与", st.is_settle_participant("公共", conn) is True)
    check("行政参与", st.is_settle_participant("行政", conn) is True)
    check("挂靠不参与", st.is_settle_participant("挂靠", conn) is False)

    # ===== 2.2 自定义类型开启参与结算 + 切换净额口径 =====
    st.add_type("自定义甲", conn=conn)
    check("自定义甲默认非参与", st.is_settle_participant("自定义甲", conn) is False)
    st.set_settle("自定义甲", True, conn=conn)
    st.set_net_basis("自定义甲", "开票净额", conn=conn)
    check("自定义甲开启后参与", st.is_settle_participant("自定义甲", conn) is True)
    check("自定义甲净额口径=开票净额", st.settle_flags_of("自定义甲", conn) == (True, "开票净额"))
    st.set_settle("自定义甲", False, conn=conn)
    check("自定义甲关闭后非参与", st.is_settle_participant("自定义甲", conn) is False)
    st.set_settle("自定义甲", True, conn=conn)
    st.set_net_basis("自定义甲", "收款净额", conn=conn)
    check("自定义甲切回收款净额", st.settle_flags_of("自定义甲", conn) == (True, "收款净额"))

    # ===== 3. 新增 / 改名 / 说明 / 排序 =====
    st.add_type("顾问", "外部顾问律师", conn=conn)
    # 预置 7 类 + 自定义甲(§2.2 已加) + 顾问(本行) = 9
    check("新增后 9 类", len(st.list_types(conn)) == 9)
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
        check("有引用拒绝删除并说明后果",
              "业务收入按 0 计" in str(e) and "保留" in str(e), f"msg={e}")
    check("拒绝后员工仍在", conn.execute(
        "SELECT COUNT(*) AS n FROM staff WHERE name='李四'").fetchone()["n"] == 1)

    conn.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
