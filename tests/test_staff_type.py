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
    if "role_code" not in [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN role_code TEXT NOT NULL DEFAULT 'other'")
    conn.commit()
    # role_def 种子（与 db.init_db 迁移块一致；去写死身份大类的语义来源）
    st.ensure_roles(conn)
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


# (类型名, 是否参与结算, 业务金额方式)：口径沿用历史预置行的取值（列默认值=收款净额）
_SEED_TYPES = (("合伙", True, "开票净额"), ("聘用", True, "收款净额"),
               ("兼职", True, "收款净额"), ("公共", True, "收款净额"),
               ("行政", True, "收款净额"), ("挂靠", False, "收款净额"),
               ("其他", False, "收款净额"))


def seed_types(conn) -> None:
    """按「用户在员工类型页自建」的等价动作铺出结算口径。

    为什么需要它：改动 1 后 `ensure_defaults()` 不再预置任何类型（员工类型表初始为
    空），所以用例用到的类型必须自己建 —— 否则"合伙/聘用"这类断言会直接 KeyError。
    这里复刻的是页面动作：add_type 新建 → 勾选参与 → 下拉选口径；
    is_builtin=1（合伙/聘用/兼职/公共/行政）与禁删禁改名语义保持一致。
    """
    # 每个种子类型的角色归属（去写死：角色决定 forbid_public_exclusive/
    # include_in_income_report/default_net_basis；类型名与角色解耦）
    _ROLE_OF = {"合伙": "partner", "聘用": "employee", "兼职": "parttime"}
    for name, settle, basis in _SEED_TYPES:
        if st.get_type(name, conn) is None:
            st.add_type(name, conn=conn)
            builtin = name in st.BUILTIN_TYPES or name in ("公共", "行政")
            conn.execute("UPDATE staff_type_def SET is_builtin=? WHERE name=?",
                         (1 if builtin else 0, name))
            conn.commit()
        st.set_role_code(name, _ROLE_OF.get(name, "other"), conn=conn)
        st.set_settle(name, settle, conn=conn)   # 参与结算开关（不动口径，顺序任意）
        st.set_net_basis(name, basis, conn=conn)


def main() -> int:
    conn = make_conn()

    # ===== 1. 建库/首次打开：不预置任何类型，类型由用户自行新建 =====
    st.ensure_defaults(conn)
    names = [t["name"] for t in st.list_types(conn)]
    check("ensure_defaults 不预置任何类型", names == [], f"got={names}")
    check("list_types 初始为空表", st.list_types(conn) == [], f"got={st.list_types(conn)}")
    # 用户自建之后口径与改动前一致（下列断言保持原语义）
    seed_types(conn)
    names = [t["name"] for t in st.list_types(conn)]
    check("自建 7 类(含公共/行政)", names == ["合伙", "聘用", "兼职", "公共", "行政", "挂靠", "其他"],
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
    # 类型不存在 → 无口径可依据，返回空串（不再臆造"收款净额"）
    check("未知类型 settle=(0,'')", st.settle_flags_of("不存在的类型", conn) == (False, ""))

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
    # 去写死（批次3）：改名锁已放开，内置类型也可改名，且角色（语义）保留
    st.add_type("临时内置", conn=conn)
    conn.execute("UPDATE staff_type_def SET is_builtin=1 WHERE name='临时内置'")
    st.set_role_code("临时内置", "partner", conn=conn)
    conn.commit()
    st.rename_type("临时内置", "临时改名", conn=conn)
    check("内置类型可改名(去写死)", st.get_type("临时改名", conn) is not None)
    check("改名后角色保留 partner", st.role_code_of("临时改名", conn) == "partner")

    st.set_note("合伙", "按开票净额计业务收入", conn=conn)
    check("内置可改说明", st.get_type("合伙", conn)["note"] == "按开票净额计业务收入")

    st.add_type("实习", conn=conn)
    order1 = [t["name"] for t in st.list_types(conn)]
    st.move_type("实习", -1, conn=conn)
    order2 = [t["name"] for t in st.list_types(conn)]
    check("上移改变顺序", order1 != order2 and order2.index("实习") < order1.index("实习"),
          f"{order1} -> {order2}")

    # ===== 4. 删除类型：有人在用禁删（去写死后「内置锁」已放开，仅保留员工引用护栏）=====
    # 注：批次3 已移除「内置三类禁止删除」锁 —— 只要无员工引用，内置类型也能删。
    conn.execute("INSERT INTO staff(name, staff_type) VALUES('张三','顾问')")
    conn.commit()
    st.add_type("顾问", conn=conn)   # 重新加回（前面改名成了外部专家）
    expect_err("有员工在用禁删", lambda: st.delete_type("顾问", conn=conn))
    # 员工改类型后可删
    conn.execute("UPDATE staff SET staff_type='聘用' WHERE name='张三'")
    conn.commit()
    st.delete_type("顾问", conn=conn)
    check("无人使用可删", "顾问" not in [t["name"] for t in st.list_types(conn)])

    # 4a 去写死回归：内置类型无员工引用时也可删（原「内置锁」已放开）
    st.add_type("临时内置删", conn=conn)
    conn.execute("UPDATE staff_type_def SET is_builtin=1 WHERE name='临时内置删'")
    conn.commit()
    st.delete_type("临时内置删", conn=conn)   # 不应抛错
    check("内置类型无员工可删(去写死)",
          "临时内置删" not in [t["name"] for t in st.list_types(conn)])

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

    # ===== 8. 回归：初始为空 / 不覆盖用户勾选 / 取消勾选清空口径 =====
    # 8a 全新库：建库后员工类型表必须完全空白
    c_new = make_conn()
    st.ensure_defaults(c_new)
    check("全新库 类型表为空", st.list_types(c_new) == [], f"got={st.list_types(c_new)}")
    check("全新库 表内 0 行", c_new.execute(
        "SELECT COUNT(*) AS n FROM staff_type_def").fetchone()["n"] == 0)
    c_new.close()

    # 8b 老库升级：缺 is_settle / net_basis 的列要能补上，且不改写已有行
    c_old = make_conn()
    # 退回"未迁移"形态（仅 4 列），再由 ensure_defaults 补列
    c_old.executescript(
        "ALTER TABLE staff_type_def RENAME TO staff_type_def_tmp;"
        "CREATE TABLE staff_type_def(name TEXT PRIMARY KEY, is_builtin INTEGER DEFAULT 0,"
        " note TEXT DEFAULT '', sort_order INTEGER DEFAULT 0);"
        " INSERT INTO staff_type_def(name, is_builtin, note, sort_order)"
        "  SELECT name, is_builtin, note, sort_order FROM staff_type_def_tmp;"
        " DROP TABLE staff_type_def_tmp;")
    # 造一行"升级前就在"的历史数据（未参与结算，也不该在补列时被改写）
    c_old.execute("INSERT INTO staff_type_def(name, is_builtin, note, sort_order)"
                  " VALUES('合伙',1,'',1)")
    c_old.commit()
    st.ensure_defaults(c_old)
    cols = [r[1] for r in c_old.execute("PRAGMA table_info(staff_type_def)")]
    check("老库补出 is_settle 列", "is_settle" in cols, f"cols={cols}")
    check("老库补出 net_basis 列", "net_basis" in cols, f"cols={cols}")
    # 存量行落在列默认值上（与 db.init_db 迁移块同一口径），不因补列被改写 is_settle
    check("老库补列后 存量行按列默认值", st.settle_flags_of("合伙", c_old) == (False, "收款净额"),
          f"got={st.settle_flags_of('合伙', c_old)}")
    c_old.close()

    # 8c ★核心回归★：取消勾选后，反复 list_types（=刷新页面）不得写回
    c_r = make_conn()
    st.add_type("外部顾问", conn=c_r)
    st.set_settle("外部顾问", True, conn=c_r)
    st.set_net_basis("外部顾问", "开票净额", conn=c_r)
    check("开启参与", st.is_settle_participant("外部顾问", c_r) is True)
    st.set_settle("外部顾问", False, conn=c_r)
    for _ in range(3):            # 模拟"打开页面 → refresh → list_types"反复调用
        st.list_types(c_r)
    row = st.get_type("外部顾问", c_r)
    check("取消勾选不被 list_types 写回",
          row["is_settle"] == 0 and st.is_settle_participant("外部顾问", c_r) is False,
          f"got={dict(row)}")
    # 取消勾选只关开关：口径按原样留在库里，且不被 "or 收款净额" 兜底悄悄还原。
    # 结算侧（person_settlement）只在 is_settle=True 时读第 2 项，故金额完全不受影响。
    check("取消勾选后 口径按原样保留", row["net_basis"] == "开票净额", f"got={row['net_basis']!r}")
    check("取消勾选后 读数不参与结算且口径原样",
          st.settle_flags_of("外部顾问", c_r) == (False, "开票净额"),
          f"got={st.settle_flags_of('外部顾问', c_r)}")

    # ★核心回归★：取消勾选 → 重新勾选 → 口径原样恢复，不得退化成默认口径（收款净额）。
    # 退化会让「合伙」这类按开票净额计的类型少算收入（开票 100 万/收 60 万 → 只计 60 万）。
    st.set_settle("外部顾问", True, conn=c_r)
    check("重新勾选后 口径未退化成默认",
          st.settle_flags_of("外部顾问", c_r) == (True, "开票净额"),
          f"got={st.settle_flags_of('外部顾问', c_r)}")

    # 内置类型同口径：公共 关掉后刷新仍保持关闭，重新勾选可再参与
    seed_types(c_r)
    st.set_settle("公共", False, conn=c_r)
    st.list_types(c_r)
    check("内置类型取消勾选不被写回", st.get_type("公共", c_r)["is_settle"] == 0)
    st.set_settle("公共", True, conn=c_r)
    check("内置类型可重新勾选参与", st.is_settle_participant("公共", c_r) is True)

    # 内置「合伙」最小复现（QA 场景）：开票 1000 未收，口径=开票净额
    check("内置 合伙 初始读数", st.settle_flags_of("合伙", c_r) == (True, "开票净额"),
          f"got={st.settle_flags_of('合伙', c_r)}")
    st.set_settle("合伙", False, conn=c_r)
    check("内置 合伙 取消勾选后 不参与且口径保留",
          st.settle_flags_of("合伙", c_r) == (False, "开票净额"),
          f"got={st.settle_flags_of('合伙', c_r)}")
    st.set_settle("合伙", True, conn=c_r)
    check("内置 合伙 重新勾选后 口径恢复（不退化为收款净额）",
          st.settle_flags_of("合伙", c_r) == (True, "开票净额"),
          f"got={st.settle_flags_of('合伙', c_r)}")

    # 8d 导入按需建类型：默认不参与，且不动已有类型的勾选
    st.ensure_types(c_r, ["返聘"])
    check("导入按需补类型", "返聘" in [t["name"] for t in st.list_types(c_r)])
    check("按需补的类型默认不参与", st.is_settle_participant("返聘", c_r) is False)
    check("按需补类型不覆盖已设勾选", st.get_type("公共", c_r)["is_settle"] == 1)
    c_r.close()

    conn.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
