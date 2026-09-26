"""费用分类改名（其他→报销摊销等）+ 公共专属费用分类与导入校验 — 内存库。

运行：python tests/test_expense_public_exclusive.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.db import _migrate_expense_category_rename  # noqa: E402
from app.engine import expense_cat as ec  # noqa: E402
from app.engine.expense_cat import PUBLIC_EXCLUSIVE_CATEGORY, validate_public_exclusive  # noqa: E402

OK, FAILS = 0, []


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # 去写死（Plan A）：角色列 + role_def 种子（复刻 init_db 迁移，幂等）
    if "role_code" not in [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]:
        conn.execute("ALTER TABLE staff_type_def ADD COLUMN role_code TEXT NOT NULL DEFAULT 'other'")
    from app.engine import staff_type as _st
    _st.ensure_roles(conn)
    return conn


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def main() -> int:
    # ===== 1. 分类结构：6 类，含「报销摊销等」「公共专属费用」，无「其他」 =====
    conn = make_conn()
    ec.ensure_categories(conn)
    names = [c["name"] for c in ec.list_categories(conn)]
    check("6 个固定分类", names == ec.CATEGORIES, f"got={names}")
    check("含 报销摊销等", "报销摊销等" in names)
    check("含 公共专属费用", PUBLIC_EXCLUSIVE_CATEGORY in names)
    check("不再有 其他 分类", "其他" not in names)

    # ===== 2. 迁移：历史「其他」→「报销摊销等」（幂等） =====
    conn2 = make_conn()
    # 模拟升级前的历史库：分类名「其他」 + 一条归到「其他」的类型
    conn2.execute("INSERT INTO expense_category(name, note, sort_order) VALUES('其他','',5)")
    conn2.execute("INSERT INTO expense_cat(expense_type, category, sort_order) VALUES('办公用品','其他',1)")
    conn2.commit()
    _migrate_expense_category_rename(conn2)
    ec.ensure_categories(conn2)
    cats2 = {c["name"] for c in ec.list_categories(conn2)}
    check("迁移后无 其他 分类", "其他" not in cats2)
    check("迁移后 报销摊销等 存在", "报销摊销等" in cats2)
    # 幂等：再跑一次不应出错、不丢数据
    _migrate_expense_category_rename(conn2)
    check("迁移幂等不丢类型",
          conn2.execute("SELECT COUNT(*) AS n FROM expense_cat WHERE expense_type='办公用品'").fetchone()["n"] == 1)
    check("迁移把类型并入 报销摊销等",
          conn2.execute("SELECT category FROM expense_cat WHERE expense_type='办公用品'").fetchone()["category"] == "报销摊销等")

    # ===== 3. 公共专属费用导入校验 =====
    conn3 = make_conn()
    ec.ensure_categories(conn3)
    # 去写死（Plan A）：建员工类型 + 角色映射（复刻 init_db 的按名迁移，仅 fixture）
    from app.engine import staff_type as _st
    for _n, _c in (("合伙", "partner"), ("聘用", "employee"), ("兼职", "parttime")):
        if _st.get_type(_n, conn3) is None:
            _st.add_type(_n, conn=conn3)
        _st.set_role_code(_n, _c, conn=conn3)
    conn3.commit()
    # 花名册
    for nm, st in [("张三", "合伙"), ("李四", "聘用"), ("王五", "兼职"), ("赵六", "挂靠"), ("钱七", "其他")]:
        conn3.execute("INSERT INTO staff(name, staff_type) VALUES(?,?)", (nm, st))
    # 费用类型：公共差旅 归到 公共专属费用；办公费 归到 报销摊销等
    ec.add_type("公共差旅", PUBLIC_EXCLUSIVE_CATEGORY, conn3)
    ec.add_type("办公费", "报销摊销等", conn3)
    conn3.commit()

    def item(et, h):
        return {"expense_type": et, "actual_handler": h}

    # 合伙/聘用/兼职 承担公共专属费用 → 命中
    viol = validate_public_exclusive(conn3, [
        item("公共差旅", "张三"),
        item("公共差旅", "李四"),
        item("公共差旅", "王五"),
    ])
    check("合伙/聘用/兼职 命中", set(viol) == {"张三(合伙)", "李四(聘用)", "王五(兼职)"}, f"got={viol}")

    # 公共/行政（白名单）、挂靠、其他 → 放行
    viol2 = validate_public_exclusive(conn3, [
        item("公共差旅", "公共"),
        item("公共差旅", "行政"),
        item("公共差旅", "赵六"),   # 挂靠
        item("公共差旅", "钱七"),   # 其他
    ])
    check("白名单/非合伙聘用兼职 放行", viol2 == [], f"got={viol2}")

    # 非公共专属费用分类（办公费）即便由合伙承担也放行
    viol3 = validate_public_exclusive(conn3, [item("办公费", "张三")])
    check("非公共专属费用 不拦截", viol3 == [], f"got={viol3}")

    # 费用类型本身就叫「公共专属费用」也会被拦（兜底，按名字命中）
    conn3.execute("INSERT INTO expense_cat(expense_type, category, sort_order) VALUES('公共专属费用','报销摊销等',9)")
    conn3.commit()
    viol4 = validate_public_exclusive(conn3, [item("公共专属费用", "张三")])
    check("类型名命中也拦截", viol4 == ["张三(合伙)"], f"got={viol4}")

    # 缺经办人 / 空类型 → 不误报
    viol5 = validate_public_exclusive(conn3, [item("公共差旅", ""), item("", "张三")])
    check("空经办人/空类型 不误报", viol5 == [], f"got={viol5}")

    print(f"\n通过 {OK} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  ✗", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
