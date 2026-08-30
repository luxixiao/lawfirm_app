"""expense_cat（费用类型 5 分类 + 顺序 + 说明）单元测试 — 内存库。

运行：python tests/test_expense_cat.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import expense_cat as ec  # noqa: E402
from app.engine.expense_cat import ExpenseCatError  # noqa: E402

OK, FAILS = 0, []


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def expect_err(label, fn, exc=ExpenseCatError):
    global OK
    try:
        fn()
        FAILS.append(f"{label}: 未抛出 {exc.__name__}")
    except exc:
        OK += 1


def main() -> int:
    conn = make_conn()

    # ===== 1. 分类初始化 =====
    cats = ec.list_categories(conn)
    check("5 个分类", [c["name"] for c in cats] == ec.CATEGORIES,
          f"got={[c['name'] for c in cats]}")
    check("分类说明默认空", all(c["note"] == "" for c in cats))
    check("分类计数默认 0", all(c["count"] == 0 for c in cats))
    # 幂等
    ec.ensure_categories(conn)
    check("ensure_categories 幂等", len(ec.list_categories(conn)) == 5)

    # ===== 2. 分类说明读写 =====
    ec.set_category_note("汽油费", "车辆相关：加油/过路/停车", conn)
    check("说明写入", ec.get_category_note("汽油费", conn) == "车辆相关：加油/过路/停车")
    check("其它类说明不受影响", ec.get_category_note("保险费", conn) == "")
    ec.set_category_note("汽油费", "  ", conn)
    check("留空清空说明", ec.get_category_note("汽油费", conn) == "")

    # ===== 3. 新增类型与默认归类 =====
    for t in ["分成（报酬发放）", "工资", "公积金", "社保", "保险费", "汽油费", "停车费", "办公用品"]:
        ec.add_type(t, ec.FALLBACK_CATEGORY, conn)
    ec.set_category("分成（报酬发放）", "报酬发放", conn)
    ec.set_category("工资", "报酬发放", conn)
    ec.set_category("公积金", "住房公积金", conn)
    ec.set_category("社保", "保险费", conn)
    ec.set_category("保险费", "保险费", conn)
    ec.set_category("汽油费", "汽油费", conn)
    ec.set_category("停车费", "汽油费", conn)
    # 办公用品留在"其他"

    by = ec.types_by_category(conn)
    check("报酬发放 2 项", by["报酬发放"] == ["分成（报酬发放）", "工资"], f"got={by['报酬发放']}")
    check("住房公积金 1 项", by["住房公积金"] == ["公积金"])
    check("保险费 2 项", by["保险费"] == ["社保", "保险费"])
    check("汽油费 2 项", by["汽油费"] == ["汽油费", "停车费"])
    check("其他 1 项", by["其他"] == ["办公用品"])

    # 分类计数
    counts = {c["name"]: c["count"] for c in ec.list_categories(conn)}
    check("计数 报酬发放=2", counts["报酬发放"] == 2, f"got={counts}")
    check("计数 其他=1", counts["其他"] == 1)

    # ===== 4. 重名 / 空名 校验 =====
    expect_err("重名新增", lambda: ec.add_type("工资", "其他", conn))
    expect_err("空名新增", lambda: ec.add_type("", "其他", conn))
    expect_err("超长名新增", lambda: ec.add_type("x" * 31, "其他", conn))

    # ===== 5. 非法归类并入兜底 =====
    ec.set_category("办公用品", "不存在的分类", conn)
    check("非法归类并入其他", ec.types_by_category(conn)["其他"] == ["办公用品"],
          f"got={ec.types_by_category(conn)}")

    # ===== 6. 改名（同步台账） =====
    conn.execute("INSERT INTO expense_ledger(period, expense_type, expense_amount)"
                 " VALUES('2025-01','停车费',100)")
    conn.commit()
    ec.rename_type("停车费", "停车及过路费", conn)
    check("改名生效", "停车及过路费" in ec.types_by_category(conn)["汽油费"])
    check("台账同步改名",
          conn.execute("SELECT COUNT(*) AS n FROM expense_ledger WHERE expense_type='停车及过路费'")
          .fetchone()["n"] == 1)
    expect_err("改名撞名", lambda: ec.rename_type("停车及过路费", "工资", conn))
    expect_err("改名空名", lambda: ec.rename_type("停车及过路费", "  ", conn))
    expect_err("改名不存在的类型", lambda: ec.rename_type("不存在", "x", conn))

    # ===== 7. 删除 + 引用计数 =====
    check("引用计数 1", ec.type_reference_count("停车及过路费", conn) == 1)
    check("引用计数 0", ec.type_reference_count("办公用品", conn) == 0)
    ec.delete_type("办公用品", conn)
    check("删除生效", "办公用品" not in ec.get_map(conn))
    expect_err("重复删除", lambda: ec.delete_type("办公用品", conn))

    # ===== 8. 类内上移/下移 =====
    before = ec.types_by_category(conn)["汽油费"]
    check("汽油费初始序", before == ["汽油费", "停车及过路费"], f"got={before}")
    ok = ec.move_in_category("停车及过路费", -1, conn)
    check("类内上移成功", ok is True)
    check("类内上移结果", ec.types_by_category(conn)["汽油费"] == ["停车及过路费", "汽油费"],
          f"got={ec.types_by_category(conn)['汽油费']}")
    ok = ec.move_in_category("停车及过路费", -1, conn)
    check("已在首位不能再上移", ok is False)
    ok = ec.move_in_category("汽油费", 1, conn)
    check("已在末位不能再下移", ok is False)

    # ===== 9. 全局顺序 = 分类顺序 + 类内顺序 =====
    ec.save_layout({
        "报酬发放": ["工资", "分成（报酬发放）"],
        "住房公积金": ["公积金"],
        "保险费": ["保险费", "社保"],
        "汽油费": ["停车及过路费", "汽油费"],
        "其他": [],
    }, conn)
    want = ["工资", "分成（报酬发放）", "公积金", "保险费", "社保",
            "停车及过路费", "汽油费"]
    check("ordered_types 全局序", ec.ordered_types(conn) == want,
          f"got={ec.ordered_types(conn)}")
    check("get_by_category 保险费序", ec.get_by_category("保险费", conn) == ["保险费", "社保"],
          f"got={ec.get_by_category('保险费', conn)}")

    # ===== 10. save_layout 漏网类型排到末尾 =====
    ec.add_type("临时杂项", "其他", conn)
    ec.save_layout({
        "报酬发放": ["工资", "分成（报酬发放）"],
        "住房公积金": ["公积金"],
        "保险费": ["保险费", "社保"],
        "汽油费": ["停车及过路费", "汽油费"],
        "其他": [],
    }, conn)
    allt = ec.ordered_types(conn)
    check("漏网类型排末尾", allt == want + ["临时杂项"], f"got={allt}")

    # ===== 11. 跨类搬运（改归类） =====
    ec.set_category("临时杂项", "报酬发放", conn)
    check("搬运后归类", ec.get_map(conn)["临时杂项"] == "报酬发放")
    check("搬运后其他类为空", ec.types_by_category(conn)["其他"] == [])

    # ===== 12. 脏数据归类（DB 里出现未知分类） =====
    conn.execute("UPDATE expense_cat SET category='历史遗留' WHERE expense_type='临时杂项'")
    conn.commit()
    check("脏数据并入其他", ec.types_by_category(conn)["其他"] == ["临时杂项"],
          f"got={ec.types_by_category(conn)}")
    check("脏数据也计入计数",
          {c["name"]: c["count"] for c in ec.list_categories(conn)}["其他"] == 1)

    # ===== 13. 台账同步（默认规则自动归类） =====
    conn2 = make_conn()
    conn2.execute("INSERT INTO expense_ledger(period, expense_type) VALUES('2025-01','公积金')")
    conn2.execute("INSERT INTO expense_ledger(period, expense_type) VALUES('2025-01','社保')")
    conn2.execute("INSERT INTO expense_ledger(period, expense_type) VALUES('2025-01','汽油')")
    conn2.execute("INSERT INTO expense_ledger(period, expense_type) VALUES('2025-01','打字复印')")
    conn2.commit()
    ec.sync_from_ledger(conn2)
    m2 = ec.get_map(conn2)
    check("同步 公积金→住房公积金", m2.get("公积金") == "住房公积金", f"got={m2}")
    check("同步 社保→保险费", m2.get("社保") == "保险费")
    check("同步 汽油→汽油费", m2.get("汽油") == "汽油费")
    check("同步 未知→其他", m2.get("打字复印") == "其他")
    check("同步后 4 类齐全", len(ec.ordered_types(conn2)) == 4)
    check("check_unknown 为空", ec.check_unknown(conn2, ["公积金", "社保"]) == [])
    check("check_unknown 命中", ec.check_unknown(conn2, ["冥王星费"]) == ["冥王星费"])

    # ===== 14. 类内移动不跨类（跨类须走 set_category / save_layout） =====
    check("单类型类内移动无效", ec.move_in_category("公积金", -1, conn2) is False)
    check("单类型类内下移无效", ec.move_in_category("公积金", 1, conn2) is False)
    ec.add_type("商业保险", "保险费", conn2)
    check("保险费 2 项", ec.types_by_category(conn2)["保险费"] == ["社保", "商业保险"],
          f"got={ec.types_by_category(conn2)['保险费']}")
    ec.move_in_category("商业保险", -1, conn2)
    check("类内上移后", ec.types_by_category(conn2)["保险费"] == ["商业保险", "社保"],
          f"got={ec.types_by_category(conn2)['保险费']}")
    check("类内移动不影响别的分类", ec.types_by_category(conn2)["住房公积金"] == ["公积金"])

    print(f"\n通过 {OK} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  ✗", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
