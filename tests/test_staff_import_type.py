"""员工类型导入（人×类型关联）与 类型设置导入（类型定义）引擎单测 — 内存库。

两个导入函数都不依赖 Qt，纯 SQLite 逻辑，可在无 PySide6 环境直接跑：
- `import_person_type_assignments(conn, rows)`  rows: [(姓名, 类型名), ...]
   返回统计 dict，遇未知人员/未知类型/重复/空值 仅跳过**不阻断整批**。
- `import_type_defs(conn, rows)`            rows: [(类型名, 开票, 报销, 业务金额方式, 说明), ...]
   返回新增条数，但**整批预校验**：内置/已存在/非法 flag/非法口径 任一命中即整批阻断、零写库。

运行：python tests/test_staff_import_type.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import staff_type as st  # noqa: E402
from app.engine.staff_type import StaffTypeError  # noqa: E402

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


def seed_types(conn) -> None:
    """铺 7 类内置/非内置类型（与 test_staff_type 同口径），供"已存在/内置"阻断用例。"""
    for name, invoice, expense, basis in (
        ("合伙", True, True, "开票净额"), ("聘用", True, True, "收款净额"),
        ("兼职", True, True, "收款净额"), ("公共", False, True, "收款净额"),
        ("行政", False, True, "收款净额"), ("挂靠", False, False, "收款净额"),
        ("其他", False, False, "收款净额"),
    ):
        if st.get_type(name, conn) is None:
            st.add_type(name, conn=conn)
            builtin = name in st.BUILTIN_TYPES or name in ("公共", "行政")
            conn.execute("UPDATE staff_type_def SET is_builtin=? WHERE name=?",
                         (1 if builtin else 0, name))
            conn.commit()
        st.set_invoice(name, invoice, conn=conn)
        st.set_can_expense(name, expense, conn=conn)
        st.set_net_basis(name, basis, conn=conn)


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
    # =====================================================================
    # 一、import_person_type_assignments（员工类型 tab 导入）
    # =====================================================================
    c = make_conn()
    seed_types(c)  # 含 合伙/聘用 等
    for nm in ("张三", "李四"):
        st.add_roster_person("", nm, "", "", "", "", "", conn=c)

    rows = [
        ("张三", "合伙"),        # 新增，张三尚无主类型 → is_primary=1
        ("张三", "聘用"),        # 新增，已有主类型 → is_primary=0
        ("李四", "合伙"),        # 新增
        ("王五", "合伙"),        # 未知人员 → 跳过
        ("张三", "合伙人"),      # 未知类型（注意：内置是"合伙"不是"合伙人"）→ 跳过
        ("", "合伙"),            # 空姓名 → 跳过
        ("张三", ""),            # 空类型 → 跳过
        ("张三", "合伙"),        # 重复 → 跳过
    ]
    stats = st.import_person_type_assignments(c, rows)
    c.commit()

    check("人-类型导入 added=3", stats["added"] == 3, f"got={stats}")
    check("人-类型导入 skipped_empty=2", stats["skipped_empty"] == 2, f"got={stats}")
    check("人-类型导入 skipped_unknown_person=1", stats["skipped_unknown_person"] == 1, f"got={stats}")
    check("人-类型导入 skipped_unknown_type=1", stats["skipped_unknown_type"] == 1, f"got={stats}")
    check("人-类型导入 skipped_dup=1", stats["skipped_dup"] == 1, f"got={stats}")

    # 入库正确性：张三两张（合伙=主，聘用=次），李四一张（合伙）
    n_map = c.execute("SELECT COUNT(*) AS n FROM staff_type_map").fetchone()["n"]
    check("人-类型导入 实际入库 3 行", n_map == 3, f"got={n_map}")
    z_pri = c.execute(
        "SELECT is_primary FROM staff_type_map WHERE name='张三' AND type_name='合伙'").fetchone()
    check("张三首类型置主类型(is_primary=1)", z_pri is not None and z_pri["is_primary"] == 1,
          f"got={z_pri}")
    z_sec = c.execute(
        "SELECT is_primary FROM staff_type_map WHERE name='张三' AND type_name='聘用'").fetchone()
    check("张三次类型(is_primary=0)", z_sec is not None and z_sec["is_primary"] == 0, f"got={z_sec}")
    # 未知人员/未知类型 不得入库
    check("未知人员王五未入库", c.execute(
        "SELECT COUNT(*) AS n FROM staff_type_map WHERE name='王五'").fetchone()["n"] == 0)
    check("未知类型合伙人未入库", c.execute(
        "SELECT COUNT(*) AS n FROM staff_type_map WHERE type_name='合伙人'").fetchone()["n"] == 0)

    # 空入参：返回全零、不报错
    empty = st.import_person_type_assignments(c, [])
    check("空入参 全 0", empty == {
        "added": 0, "skipped_empty": 0, "skipped_unknown_person": 0,
        "skipped_unknown_type": 0, "skipped_dup": 0}, f"got={empty}")
    c.close()

    # =====================================================================
    # 二、import_type_defs（类型设置 tab 导入）—— 正常插入
    # =====================================================================
    d = make_conn()  # 空类型表，无冲突
    stats = st.import_type_defs(d, [
        ("顾问", "是", "否", "开票净额", "外部顾问律师"),
        ("返聘", "否", "是", "", ""),
    ])
    check("类型设置导入 新增 2 条", stats["added"] == 2, f"got={stats}")
    g = st.get_type("顾问", d)
    check("顾问 is_invoice=1", g["is_invoice"] == 1, f"got={dict(g)}")
    check("顾问 can_expense=0", g["can_expense"] == 0, f"got={dict(g)}")
    check("顾问 net_basis=开票净额", g["net_basis"] == "开票净额", f"got={dict(g)}")
    check("顾问 is_builtin=0", g["is_builtin"] == 0, f"got={dict(g)}")
    f = st.get_type("返聘", d)
    check("返聘 is_invoice=0", f["is_invoice"] == 0, f"got={dict(f)}")
    check("返聘 can_expense=1", f["can_expense"] == 1, f"got={dict(f)}")
    check("返聘 未开票 口径留空", f["net_basis"] == "", f"got={dict(f)}")
    d.close()

    # 开票=是且口径留空 → 引擎默认 收款净额
    d2 = make_conn()
    n = st.import_type_defs(d2, [("新甲", "是", "否", "", "")])
    check("开票=是空口径 默认收款净额", n["added"] == 1, f"got={n}")
    x = st.get_type("新甲", d2)
    check("新甲 net_basis=收款净额", x["net_basis"] == "收款净额", f"got={dict(x)}")
    check("新甲 is_invoice=1", x["is_invoice"] == 1, f"got={dict(x)}")
    d2.close()

    # =====================================================================
    # 三、import_type_defs —— 整批阻断（四类，均零写库）
    # =====================================================================
    # 3a 已存在(含内置)类型名 → 跳过保留、不阻断；新类型仍新增
    db = make_conn()
    seed_types(db)  # 含 合伙(内置, is_invoice=1) / 挂靠(非内置)
    before = db.execute("SELECT COUNT(*) AS n FROM staff_type_def").fetchone()["n"]
    stats = st.import_type_defs(db, [
        ("合伙", "否", "否", "收款净额", ""),   # 已存在内置，文件与之不同(开票改否) → 跳过保留库值
        ("挂靠", "否", "是", "", ""),           # 已存在非内置 → 跳过
        ("新类型", "是", "否", "开票净额", ""),  # 新 → 新增
    ])
    after = db.execute("SELECT COUNT(*) AS n FROM staff_type_def").fetchone()["n"]
    check("已存在类型跳过不阻断", stats["skipped_existing"] == 2, f"got={stats}")
    check("新类型仍新增", stats["added"] == 1, f"got={stats}")
    check("已存在跳过 库仅+1新类型", after == before + 1, f"before={before} after={after}")
    # 库里合伙保持原值(is_invoice=1)，不被文件(否)改写 → 内置/既有权限受保护
    check("合伙原配置保留不被改写", st.get_type("合伙", db)["is_invoice"] == 1,
          f"got={dict(st.get_type('合伙', db))}")
    check("新类型已入库", st.get_type("新类型", db) is not None)
    db.close()

    # 3b 用户真实场景：文件含 7 个已存在标准类型 + 4 个新类型 → 仅新增 4 个
    db = make_conn()
    seed_types(db)
    rows = [
        ("合伙", "否", "否", "开票净额", ""), ("聘用", "否", "否", "收款净额", ""),
        ("兼职", "否", "否", "收款净额", ""), ("行政", "否", "否", "收款净额", ""),
        ("公共", "否", "否", "收款净额", ""), ("挂靠", "否", "否", "收款净额", ""),
        ("其他", "否", "否", "收款净额", ""),
        ("实习", "否", "否", "收款净额", ""), ("家属", "否", "否", "收款净额", ""),
        ("单独项目", "否", "否", "收款净额", ""), ("6666", "否", "否", "", ""),
    ]
    stats = st.import_type_defs(db, rows)
    check("真实场景 跳过已存在=7", stats["skipped_existing"] == 7, f"got={stats}")
    check("真实场景 新增=4", stats["added"] == 4, f"got={stats}")
    check("真实场景 6666 已入库", st.get_type("6666", db) is not None)
    check("真实场景 实习 已入库", st.get_type("实习", db) is not None)
    check("真实场景 合伙仍保留(is_invoice=1)", st.get_type("合伙", db)["is_invoice"] == 1)
    db.close()

    # 3b2 文件内重名新类型 → 只建一次，多余行计入 skipped_dup
    db = make_conn()
    stats = st.import_type_defs(db, [
        ("重名甲", "是", "否", "开票净额", ""),
        ("重名甲", "否", "否", "收款净额", ""),  # 同名再次出现 → 跳过
    ])
    check("文件内重名 仅新增 1", stats["added"] == 1, f"got={stats}")
    check("文件内重名 skipped_dup=1", stats["skipped_dup"] == 1, f"got={stats}")
    db.close()

    # 3c 无法识别的开票值 → 整批阻止
    db = make_conn()
    expect_err("非法开票值整批阻止",
               lambda: st.import_type_defs(db, [
                   ("新甲", "也许", "否", "", ""),
                   ("新乙", "是", "否", "开票净额", ""),
               ]))
    check("非法开票值阻断后 库无新类型", db.execute(
        "SELECT COUNT(*) AS n FROM staff_type_def").fetchone()["n"] == 0)
    db.close()

    # 3d 非法业务金额方式（开票=是 时） → 整批阻止
    db = make_conn()
    expect_err("非法口径整批阻止",
               lambda: st.import_type_defs(db, [
                   ("新甲", "是", "否", "净额", ""),  # 不在 {开票净额,收款净额}
                   ("新乙", "否", "是", "", ""),
               ]))
    check("非法口径阻断后 库无新类型", db.execute(
        "SELECT COUNT(*) AS n FROM staff_type_def").fetchone()["n"] == 0)
    db.close()

    # 3e 整批原子性：一好一坏 → 二者都不插入
    db = make_conn()
    expect_err("混合批（好+坏）整批阻止",
               lambda: st.import_type_defs(db, [
                   ("新甲", "是", "否", "开票净额", ""),
                   ("新乙", "x", "否", "", ""),  # 坏
               ]))
    check("混合批原子性 零写库", db.execute(
        "SELECT COUNT(*) AS n FROM staff_type_def").fetchone()["n"] == 0)
    db.close()

    # 3f flag 解析覆盖：是/否/1/0/true/false/开/关 均不阻断
    db = make_conn()
    cnt = st.import_type_defs(db, [
        ("A", "是", "否", "开票净额", ""),
        ("B", "否", "是", "", ""),
        ("C", "1", "0", "收款净额", ""),
        ("D", "0", "1", "", ""),
        ("E", "true", "false", "开票净额", ""),
        ("F", "false", "true", "", ""),
        ("G", "开", "关", "开票净额", ""),
        ("H", "关", "开", "", ""),
    ])
    check("各类 flag 均被识别 新增 8 条", cnt["added"] == 8, f"got={cnt}")
    check("A 开票=1", st.get_type("A", db)["is_invoice"] == 1)
    check("B 开票=0", st.get_type("B", db)["is_invoice"] == 0)
    check("C 报销=0", st.get_type("C", db)["can_expense"] == 0)
    db.close()

    # =====================================================================
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
