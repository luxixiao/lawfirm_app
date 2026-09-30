"""专属费用（原「公共专属费用」）改名 + 分类级白名单校验 — 内存库。

运行：python tests/test_expense_exclusive.py
（纯逻辑、无 Qt 依赖，可在无头环境跑；UI 门禁仍须在本机 venv 跑。）

覆盖：
- 6 分类含「专属费用」，无「公共专属费用」；
- 迁移：历史「其他」→「报销摊销等」（_migrate_expense_category_rename）；
- 迁移：历史「公共专属费用」→「专属费用」（_migrate_exclusive_category_rename，幂等）；
- validate_exclusive：分类级白名单（方案乙）
  · 在白名单内的员工类型可承担，否则违例（返回 姓名(主类型)）；
  · 一人多类型按 OR 聚合（任一类型在白名单即放行）；
  · 非花名册经办人（如公共/行政）豁免放行；
  · 白名单为空 → D5 无限制，全员放行；
  · 非专属费用分类不触发；空经办人/空类型不误报。
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.db import (_migrate_expense_category_rename,  # noqa: E402
                    _migrate_exclusive_category_rename)  # noqa: E402
from app.engine import expense_cat as ec  # noqa: E402
from app.engine.expense_cat import (EXCLUSIVE_CATEGORY, validate_exclusive,  # noqa: E402
                                    get_exclusive_types, set_exclusive_types)  # noqa: E402

OK, FAILS = 0, []


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # 两条线模型后 staff_type_def 列由迁移补齐；内存库单测手动补（与生产 init_db 一致）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]
    # 专属费用白名单列（生产由 SCHEMA 已含；此处兜底，防止旧 SCHEMA 副本缺列）
    ccols = [r[1] for r in conn.execute("PRAGMA table_info(expense_category)")]
    if "exclusive_types" not in ccols:
        conn.execute("ALTER TABLE expense_category ADD COLUMN exclusive_types TEXT NOT NULL DEFAULT ''")
    from app.engine import staff_type as _st
    # 两条线模型列（is_invoice/can_expense/net_basis）：生产由 init_db 迁移补齐；
    # 内存库单测在此手动补，与生产迁移保持一致，否则 staff_type.add_type 等会因缺列报错。
    tcols = [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]
    if "is_invoice" not in tcols:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN is_invoice INTEGER NOT NULL DEFAULT 0")
    if "can_expense" not in tcols:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN can_expense INTEGER NOT NULL DEFAULT 0")
    if "net_basis" not in tcols:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN net_basis TEXT NOT NULL DEFAULT '收款净额'")
    return conn


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def seed_types(conn):
    """建 5 个人员类型（合伙/聘用/兼职/挂靠/其他），幂等。"""
    from app.engine import staff_type as _st
    for n in ("合伙", "聘用", "兼职", "挂靠", "其他"):
        if _st.get_type(n, conn) is None:
            _st.add_type(n, conn=conn)
    conn.commit()


def seed_roster(conn):
    """花名册 + 主类型关联；公共/行政 故意不入花名册（外部经办人，测豁免）。"""
    rows = [
        ("张三", "合伙"), ("李四", "聘用"), ("王五", "兼职"),
        ("赵六", "挂靠"), ("钱七", "其他"),
        ("孙八", "挂靠"),   # 主类型 挂靠，但额外有 合伙（OR 测试）
    ]
    for nm, tp in rows:
        conn.execute("INSERT OR IGNORE INTO staff_roster(name) VALUES(?)", (nm,))
        conn.execute("INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) "
                     "VALUES(?,?,1)", (nm, tp))
    # 孙八 额外挂 合伙（主类型仍 挂靠）
    conn.execute("INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) "
                 "VALUES('孙八','合伙',0)")
    conn.commit()


def item(et, h):
    return {"expense_type": et, "actual_handler": h}


def main() -> int:
    # ===== 1. 分类结构：6 类，含「专属费用」，无「公共专属费用」 =====
    conn = make_conn()
    ec.ensure_categories(conn)
    names = [c["name"] for c in ec.list_categories(conn)]
    check("6 个固定分类", names == ec.CATEGORIES, f"got={names}")
    check("含 专属费用", EXCLUSIVE_CATEGORY in names)
    check("不再有 公共专属费用", "公共专属费用" not in names)
    check("不再有 其他 分类", "其他" not in names)

    # ===== 2. 迁移：历史「其他」→「报销摊销等」（幂等） =====
    conn2 = make_conn()
    conn2.execute("INSERT INTO expense_category(name, note, sort_order) VALUES('其他','',5)")
    conn2.execute("INSERT INTO expense_cat(expense_type, category, sort_order) VALUES('办公用品','其他',1)")
    conn2.commit()
    _migrate_expense_category_rename(conn2)
    ec.ensure_categories(conn2)
    cats2 = {c["name"] for c in ec.list_categories(conn2)}
    check("迁移后无 其他 分类", "其他" not in cats2)
    check("迁移后 报销摊销等 存在", "报销摊销等" in cats2)
    _migrate_expense_category_rename(conn2)
    check("迁移幂等不丢类型",
          conn2.execute("SELECT COUNT(*) AS n FROM expense_cat WHERE expense_type='办公用品'").fetchone()["n"] == 1)
    check("迁移把类型并入 报销摊销等",
          conn2.execute("SELECT category FROM expense_cat WHERE expense_type='办公用品'").fetchone()["category"] == "报销摊销等")

    # ===== 3. 迁移：历史「公共专属费用」→「专属费用」（幂等，且不产生重复行） =====
    conn3 = make_conn()
    conn3.execute("INSERT INTO expense_category(name, note, sort_order) VALUES('公共专属费用','',6)")
    conn3.execute("INSERT INTO expense_cat(expense_type, category, sort_order) VALUES('物业公摊','公共专属费用',1)")
    conn3.commit()
    _migrate_exclusive_category_rename(conn3)
    cat_names = [r["name"] for r in conn3.execute("SELECT name FROM expense_category")]
    check("迁移后无 公共专属费用 分类", "公共专属费用" not in cat_names)
    check("迁移后 专属费用 分类存在", EXCLUSIVE_CATEGORY in cat_names)
    check("迁移把类型并入 专属费用",
          conn3.execute("SELECT category FROM expense_cat WHERE expense_type='物业公摊'").fetchone()["category"] == EXCLUSIVE_CATEGORY)
    # 幂等：再跑一次不应报错、不出现第二个「专属费用」
    _migrate_exclusive_category_rename(conn3)
    dup = conn3.execute("SELECT COUNT(*) AS n FROM expense_category WHERE name=?", (EXCLUSIVE_CATEGORY,)).fetchone()["n"]
    check("迁移幂等不产生重复分类行", dup == 1, f"got={dup}")

    # ===== 4. 专属费用导入校验（分类级白名单，方案乙） =====
    conn4 = make_conn()
    ec.ensure_categories(conn4)
    seed_types(conn4)
    seed_roster(conn4)
    # 费用类型：物业公摊 归到 专属费用；办公费 归到 报销摊销等
    ec.add_type("物业公摊", EXCLUSIVE_CATEGORY, conn4)
    ec.add_type("办公费", "报销摊销等", conn4)
    conn4.commit()
    # 「专属费用」分类白名单 = 内置三类
    set_exclusive_types(EXCLUSIVE_CATEGORY, ["合伙", "聘用", "兼职"], conn4)
    check("读回白名单", get_exclusive_types(EXCLUSIVE_CATEGORY, conn4) == ["合伙", "聘用", "兼职"])

    # 在白名单内的员工类型（合伙/聘用/兼职）→ 放行
    viol = validate_exclusive(conn4, [
        item("物业公摊", "张三"),
        item("物业公摊", "李四"),
        item("物业公摊", "王五"),
    ])
    check("白名单内三人 全部放行", viol == [], f"got={viol}")

    # 花名册内但不在白名单（挂靠/其他）→ 违例
    viol2 = validate_exclusive(conn4, [
        item("物业公摊", "赵六"),   # 挂靠
        item("物业公摊", "钱七"),   # 其他
    ])
    check("挂靠/其他 命中专属费用",
          set(viol2) == {"赵六(挂靠)", "钱七(其他)"}, f"got={viol2}")

    # 一人多类型按 OR 聚合：孙八 主类型挂靠，但另有 合伙（在白名单）→ 放行
    viol3 = validate_exclusive(conn4, [item("物业公摊", "孙八")])
    check("多类型 OR 聚合放行", viol3 == [], f"got={viol3}")

    # 非花名册经办人（公共/行政）→ 豁免放行
    viol4 = validate_exclusive(conn4, [
        item("物业公摊", "公共"),
        item("物业公摊", "行政"),
    ])
    check("非花名册经办人 豁免放行", viol4 == [], f"got={viol4}")

    # 非专属费用分类（办公费）即便由挂靠承担也放行
    viol5 = validate_exclusive(conn4, [item("办公费", "赵六")])
    check("非专属费用分类 不拦截", viol5 == [], f"got={viol5}")

    # 类型名本身就叫「专属费用」也会被拦（兜底，按名字命中）
    conn4.execute("INSERT INTO expense_cat(expense_type, category, sort_order) VALUES('专属费用','报销摊销等',9)")
    conn4.commit()
    viol6 = validate_exclusive(conn4, [item("专属费用", "赵六")])
    check("类型名命中也拦截", viol6 == ["赵六(挂靠)"], f"got={viol6}")

    # 空经办人 / 空类型 → 不误报
    viol7 = validate_exclusive(conn4, [item("物业公摊", ""), item("", "张三")])
    check("空经办人/空类型 不误报", viol7 == [], f"got={viol7}")

    # D5：清空白名单 → 无限制，全员放行（含挂靠/其他）
    set_exclusive_types(EXCLUSIVE_CATEGORY, [], conn4)
    viol8 = validate_exclusive(conn4, [
        item("物业公摊", "赵六"),
        item("物业公摊", "钱七"),
        item("物业公摊", "公共"),
    ])
    check("D5 未指定=无限制", viol8 == [], f"got={viol8}")

    print(f"\n通过 {OK} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  ✗", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
