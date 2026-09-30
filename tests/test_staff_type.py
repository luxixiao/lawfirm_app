"""staff_type（员工类型维护 + 员工删除引用检查）单元测试 — 内存库。

两条线模型（2026-09-29 重构）下的口径：
- `is_invoice`（是否开票 / 业务线，只管结算收入，不碰费用承担）
- `can_expense`（是否报销 / 费用线，唯一费用承担闸门）
- 进报表 = `is_invoice OR can_expense`（派生，不新增独立开关）
- `net_basis` ∈ {'开票净额','收款净额'}，仅 `is_invoice=1` 时有意义，否则空。

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
    # 两条线列迁移（与 db.init_db 迁移块一致，幂等）：SCHEMA 不含这两列，
    # 内存库须手动补 is_invoice / can_expense / net_basis。
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]
    for col, ddl in (("is_invoice", "INTEGER NOT NULL DEFAULT 0"),
                     ("can_expense", "INTEGER NOT NULL DEFAULT 0"),
                     ("net_basis", "TEXT NOT NULL DEFAULT '收款净额'")):
        if col not in cols:
            conn.execute(f"ALTER TABLE staff_type_def ADD COLUMN {col} {ddl}")
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


# (类型名, 是否开票(业务线), 是否报销(费用线), 业务金额方式)
# 列默认值=收款净额；映射自旧 is_settle 拆分回填口径：
#   partner/employee/parttime → 业务线开；原参与结算者（含公共/行政）→ 费用线开。
_SEED_TYPES = (("合伙", True, True, "开票净额"), ("聘用", True, True, "收款净额"),
               ("兼职", True, True, "收款净额"), ("公共", False, True, "收款净额"),
               ("行政", False, True, "收款净额"), ("挂靠", False, False, "收款净额"),
               ("其他", False, False, "收款净额"))


def seed_types(conn) -> None:
    """按「用户在员工类型页自建」的等价动作铺出两线口径。

    为什么需要它：改动后 `ensure_defaults()` 不再预置任何类型（员工类型表初始为
    空），所以用例用到的类型必须自己建 —— 否则"合伙/聘用"这类断言会直接 KeyError。
    这里复刻的是页面动作：add_type 新建 → 勾选开票 / 勾选报销 → 下拉选口径；
    is_builtin=1（合伙/聘用/兼职/公共/行政）与禁删禁改名语义保持一致。
    """
    for name, invoice, expense, basis in _SEED_TYPES:
        if st.get_type(name, conn) is None:
            st.add_type(name, conn=conn)
            builtin = name in st.BUILTIN_TYPES or name in ("公共", "行政")
            conn.execute("UPDATE staff_type_def SET is_builtin=? WHERE name=?",
                         (1 if builtin else 0, name))
            conn.commit()
        st.set_invoice(name, invoice, conn=conn)      # 业务线开关
        st.set_can_expense(name, expense, conn=conn)  # 费用线开关
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
    # 内置三类开票且净额口径正确（迁移/默认值保证）
    check("合伙 is_invoice=1 且 开票净额",
          st.get_type("合伙", conn)["is_invoice"] == 1
          and st.get_type("合伙", conn)["net_basis"] == "开票净额")
    check("公共 can_expense=1（仅费用线）", st.get_type("公共", conn)["can_expense"] == 1)
    # list_types 现在透出两线列 / net_basis（T5 UI 直接读，不再派生）
    lt = {t["name"]: t for t in st.list_types(conn)}
    check("list_types 透出 is_invoice", "is_invoice" in lt["合伙"])
    check("list_types 透出 can_expense", "can_expense" in lt["合伙"])
    check("list_types 透出 net_basis", "net_basis" in lt["合伙"])
    check("list_types 合伙 is_invoice=1", lt["合伙"]["is_invoice"] == 1)
    check("list_types 合伙 can_expense=1", lt["合伙"]["can_expense"] == 1)

    # ===== 2. 业务线口径（is_computable 改为读 is_invoice）=====
    # 注：单参 is_computable(name) 仅用于生产（staff_view.py），其内部走真实库；
    # 此处内存库测试须显式传 conn，避免落到未迁移的真实库文件。
    check("合伙进业务线", st.is_computable("合伙", conn) is True)
    check("聘用进业务线", st.is_computable("聘用", conn) is True)
    check("兼职进业务线", st.is_computable("兼职", conn) is True)
    check("公共不进业务线(仅费用线)", st.is_computable("公共", conn) is False)
    check("行政不进业务线(仅费用线)", st.is_computable("行政", conn) is False)
    check("顾问不进业务线", st.is_computable("顾问", conn) is False)
    check("其他不进业务线", st.is_computable("其他", conn) is False)
    # 旧启发式（含"合伙"字样即参与）已退休：自定义名未开启业务线 → False
    check("含'合伙'字样的自定义名不进业务线（未开启）", st.is_computable("外部合伙", conn) is False)

    # ===== 2.1 business_flags_of（单类型业务线读数）=====
    check("合伙 flags=(1,开票净额)", st.business_flags_of("合伙", conn) == (True, "开票净额"))
    check("聘用 flags=(1,收款净额)", st.business_flags_of("聘用", conn) == (True, "收款净额"))
    check("兼职 flags=(1,收款净额)", st.business_flags_of("兼职", conn) == (True, "收款净额"))
    check("公共 flags=(0,收款净额)", st.business_flags_of("公共", conn) == (False, "收款净额"))
    check("行政 flags=(0,收款净额)", st.business_flags_of("行政", conn) == (False, "收款净额"))
    check("挂靠 flags=(0,收款净额)", st.business_flags_of("挂靠", conn) == (False, "收款净额"))
    check("其他 flags=(0,收款净额)", st.business_flags_of("其他", conn) == (False, "收款净额"))
    # 类型不存在 → 无口径可依据，返回空串（不再臆造"收款净额"）
    check("未知类型 flags=(0,'')", st.business_flags_of("不存在的类型", conn) == (False, ""))

    # ===== 2.2 费用线闸门 can_bear_expense（规则③，D1 一人多类型取 OR）=====
    check("空名不可承担费用", st.can_bear_expense("", conn) is False)
    check("空名(None)不可承担费用", st.can_bear_expense(None, conn) is False)
    check("合伙可承担费用", st.can_bear_expense("合伙", conn) is True)
    check("公共可承担费用", st.can_bear_expense("公共", conn) is True)
    check("行政可承担费用", st.can_bear_expense("行政", conn) is True)
    check("挂靠不可承担费用", st.can_bear_expense("挂靠", conn) is False)
    check("其他不可承担费用", st.can_bear_expense("其他", conn) is False)

    # ===== 2.3 两条线独立开关：开启业务线不影响费用线，反之亦然 =====
    st.add_type("自定义甲", conn=conn)
    check("自定义甲默认两线均关", st.is_computable("自定义甲", conn) is False
          and st.can_bear_expense("自定义甲", conn) is False)
    st.set_invoice("自定义甲", True, conn=conn)
    st.set_net_basis("自定义甲", "开票净额", conn=conn)
    check("自定义甲开启业务线", st.is_computable("自定义甲", conn) is True)
    check("自定义甲开启业务线后 净额口径=开票净额",
          st.business_flags_of("自定义甲", conn) == (True, "开票净额"))
    check("自定义甲开业务线 不影响费用线（仍不可承担费用）",
          st.can_bear_expense("自定义甲", conn) is False)
    st.set_can_expense("自定义甲", True, conn=conn)
    check("自定义甲开启费用线后可承担费用", st.can_bear_expense("自定义甲", conn) is True)
    check("自定义甲开费用线 不影响业务线", st.is_computable("自定义甲", conn) is True)
    st.set_invoice("自定义甲", False, conn=conn)
    check("自定义甲关业务线后不进业务线", st.is_computable("自定义甲", conn) is False)
    check("自定义甲关业务线后 费用线仍在（可承担费用）",
          st.can_bear_expense("自定义甲", conn) is True)
    check("自定义甲关业务线后 口径按原样保留",
          st.business_flags_of("自定义甲", conn) == (False, "开票净额"))
    st.set_net_basis("自定义甲", "收款净额", conn=conn)
    st.set_invoice("自定义甲", True, conn=conn)
    check("自定义甲重新开业务线 切回收款净额",
          st.business_flags_of("自定义甲", conn) == (True, "收款净额"))

    # ===== 3. 新增 / 改名 / 说明 / 排序 =====
    st.add_type("顾问", "外部顾问律师", conn=conn)
    # 预置 7 类 + 自定义甲(§2.3 已加) + 顾问(本行) = 9
    check("新增后 9 类", len(st.list_types(conn)) == 9)
    expect_err("重名拒绝", lambda: st.add_type("顾问", conn=conn))
    expect_err("空名拒绝", lambda: st.add_type("  ", conn=conn))

    st.rename_type("顾问", "外部专家", conn=conn)
    check("改名生效", "外部专家" in [t["name"] for t in st.list_types(conn)])
    # 去写死（批次3）：改名锁已放开，内置类型也可改名，且业务配置（is_invoice 等）保留
    st.add_type("临时内置", conn=conn)
    conn.execute("UPDATE staff_type_def SET is_builtin=1 WHERE name='临时内置'")
    st.set_invoice("临时内置", True, conn=conn)   # 业务配置直接由开关设定（不再经角色）
    conn.commit()
    st.rename_type("临时内置", "临时改名", conn=conn)
    check("内置类型可改名(去写死)", st.get_type("临时改名", conn) is not None)
    check("改名后业务配置保留", st.business_flags_of("临时改名", conn)[0] is True)

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
    conn.execute("INSERT OR IGNORE INTO staff_roster(name) VALUES('张三')")
    conn.execute("INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) VALUES('张三','顾问',1)")
    conn.commit()
    st.add_type("顾问", conn=conn)   # 重新加回（前面改名成了外部专家）
    expect_err("有员工在用禁删", lambda: st.delete_type("顾问", conn=conn))
    # 员工改类型后可删
    conn.execute("UPDATE staff_type_map SET type_name='聘用' WHERE name='张三' AND is_primary=1")
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
        "SELECT COUNT(*) AS n FROM staff_roster WHERE name='张三'").fetchone()["n"] == 0)

    # 造引用：发票经办人 + 收款 + 费用 + 工资
    conn.execute("INSERT OR IGNORE INTO staff_roster(name) VALUES('李四')")
    conn.execute("INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) VALUES('李四','合伙',1)")
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
        "SELECT COUNT(*) AS n FROM staff_roster WHERE name='李四'").fetchone()["n"] == 1)

    # ===== 8. 回归：初始为空 / 不覆盖用户勾选 / 取消勾选清空口径 =====
    # 8a 全新库：建库后员工类型表必须完全空白
    c_new = make_conn()
    st.ensure_defaults(c_new)
    check("全新库 类型表为空", st.list_types(c_new) == [], f"got={st.list_types(c_new)}")
    check("全新库 表内 0 行", c_new.execute(
        "SELECT COUNT(*) AS n FROM staff_type_def").fetchone()["n"] == 0)
    c_new.close()

    # 8b 老库升级：缺 is_invoice / can_expense / net_basis 的列要能补上，且不改写已有行
    c_old = make_conn()
    # 退回"未迁移"形态（仅 4 基础列），再由 ensure_defaults 补列
    c_old.executescript(
        "ALTER TABLE staff_type_def RENAME TO staff_type_def_tmp;"
        "CREATE TABLE staff_type_def(name TEXT PRIMARY KEY, is_builtin INTEGER DEFAULT 0,"
        " note TEXT DEFAULT '', sort_order INTEGER DEFAULT 0);"
        " INSERT INTO staff_type_def(name, is_builtin, note, sort_order)"
        "  SELECT name, is_builtin, note, sort_order FROM staff_type_def_tmp;"
        " DROP TABLE staff_type_def_tmp;")
    # 造一行"升级前就在"的历史数据（未开票、未报销，也不该在补列时被改写）
    c_old.execute("INSERT INTO staff_type_def(name, is_builtin, note, sort_order)"
                  " VALUES('合伙',1,'',1)")
    c_old.commit()
    st.ensure_defaults(c_old)
    cols = [r[1] for r in c_old.execute("PRAGMA table_info(staff_type_def)")]
    check("老库补出 is_invoice 列", "is_invoice" in cols, f"cols={cols}")
    check("老库补出 can_expense 列", "can_expense" in cols, f"cols={cols}")
    check("老库补出 net_basis 列", "net_basis" in cols, f"cols={cols}")
    # 存量行落在列默认值上（与 db.init_db 迁移块同一口径），不因补列被改写
    check("老库补列后 存量行按列默认值", st.business_flags_of("合伙", c_old) == (False, "收款净额"),
          f"got={st.business_flags_of('合伙', c_old)}")
    c_old.close()

    # 8c ★核心回归★：取消勾选后，反复 list_types（=刷新页面）不得写回
    c_r = make_conn()
    st.add_type("外部顾问", conn=c_r)
    st.set_invoice("外部顾问", True, conn=c_r)
    st.set_net_basis("外部顾问", "开票净额", conn=c_r)
    check("开启业务线", st.is_computable("外部顾问", c_r) is True)
    st.set_invoice("外部顾问", False, conn=c_r)
    for _ in range(3):            # 模拟"打开页面 → refresh → list_types"反复调用
        st.list_types(c_r)
    row = st.get_type("外部顾问", c_r)
    check("取消勾选不被 list_types 写回",
          row["is_invoice"] == 0 and st.is_computable("外部顾问", c_r) is False,
          f"got={dict(row)}")
    # 取消勾选只关开关：口径按原样留在库里，且不被 "or 收款净额" 兜底悄悄还原。
    # 结算侧（person_settlement）只在 is_invoice=True 时读第 2 项，故金额完全不受影响。
    check("取消勾选后 口径按原样保留", row["net_basis"] == "开票净额", f"got={row['net_basis']!r}")
    check("取消勾选后 读数不进业务线且口径原样",
          st.business_flags_of("外部顾问", c_r) == (False, "开票净额"),
          f"got={st.business_flags_of('外部顾问', c_r)}")

    # ★核心回归★：取消勾选 → 重新勾选 → 口径原样恢复，不得退化成默认口径（收款净额）。
    # 退化会让「合伙」这类按开票净额计的类型少算收入（开票 100 万/收 60 万 → 只计 60 万）。
    st.set_invoice("外部顾问", True, conn=c_r)
    check("重新勾选后 口径未退化成默认",
          st.business_flags_of("外部顾问", c_r) == (True, "开票净额"),
          f"got={st.business_flags_of('外部顾问', c_r)}")

    # 内置类型同口径：公共 开启业务线→关掉→刷新仍保持关闭，重新勾选可再进业务线
    seed_types(c_r)
    st.set_invoice("公共", True, conn=c_r)
    st.list_types(c_r)
    check("内置类型开启业务线后 is_invoice=1", st.get_type("公共", c_r)["is_invoice"] == 1)
    st.set_invoice("公共", False, conn=c_r)
    st.list_types(c_r)
    check("内置类型取消勾选不被写回", st.get_type("公共", c_r)["is_invoice"] == 0)
    st.set_invoice("公共", True, conn=c_r)
    check("内置类型可重新勾选进业务线", st.is_computable("公共", c_r) is True)

    # 内置「合伙」最小复现（QA 场景）：开票 1000 未收，口径=开票净额
    check("内置 合伙 初始读数", st.business_flags_of("合伙", c_r) == (True, "开票净额"),
          f"got={st.business_flags_of('合伙', c_r)}")
    st.set_invoice("合伙", False, conn=c_r)
    check("内置 合伙 取消勾选后 不进业务线且口径保留",
          st.business_flags_of("合伙", c_r) == (False, "开票净额"),
          f"got={st.business_flags_of('合伙', c_r)}")
    st.set_invoice("合伙", True, conn=c_r)
    check("内置 合伙 重新勾选后 口径恢复（不退化为收款净额）",
          st.business_flags_of("合伙", c_r) == (True, "开票净额"),
          f"got={st.business_flags_of('合伙', c_r)}")

    # 8d 导入按需建类型：默认两线均关，且不动已有类型的勾选
    st.ensure_types(c_r, ["返聘"])
    check("导入按需补类型", "返聘" in [t["name"] for t in st.list_types(c_r)])
    check("按需补的类型默认不进业务线", st.is_computable("返聘", c_r) is False)
    check("按需补的类型默认不可承担费用", st.can_bear_expense("返聘", c_r) is False)
    check("按需补类型不覆盖已设勾选", st.get_type("公共", c_r)["can_expense"] == 1)
    c_r.close()

    conn.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
