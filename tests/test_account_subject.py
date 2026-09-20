"""会计科目（account_subject 引擎）单元测试 — 内存库。

运行：python tests/test_account_subject.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import account_subject as asub  # noqa: E402
from app.engine.account_subject import AccountSubjectError  # noqa: E402

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


def expect_err(label, fn, exc=AccountSubjectError):
    global OK
    try:
        fn()
        FAILS.append(f"{label}: 未抛出 {exc.__name__}")
    except exc:
        OK += 1


def main() -> int:
    conn = make_conn()

    # ===== 1. 空树 =====
    check("初始空树", asub.get_tree(conn) == [])
    check("空集合", asub.subject_sets(conn) == (set(), {}))

    # ===== 2. 增：一级 + 二级 =====
    p1 = asub.add_level1("资产类", conn=conn)
    p2 = asub.add_level1("费用类", conn=conn)
    c1 = asub.add_level2(p1, "办公费", conn=conn)
    c2 = asub.add_level2(p1, "差旅费", conn=conn)
    c3 = asub.add_level2(p2, "招待费", conn=conn)
    tree = asub.get_tree(conn)
    check("一级顺序", [p["name"] for p in tree] == ["资产类", "费用类"], f"got={[p['name'] for p in tree]}")
    check("资产类二级", [c["name"] for c in tree[0]["children"]] == ["办公费", "差旅费"])
    check("费用类二级", [c["name"] for c in tree[1]["children"]] == ["招待费"])

    # ===== 3. 校验：重名 / 空名 / 超长 =====
    expect_err("重名一级", lambda: asub.add_level1("资产类", conn=conn))
    expect_err("重名二级", lambda: asub.add_level2(p1, "办公费", conn=conn))
    expect_err("空名一级", lambda: asub.add_level1("", conn=conn))
    expect_err("空名二级", lambda: asub.add_level2(p1, "", conn=conn))
    expect_err("超长一级", lambda: asub.add_level1("x" * 31, conn=conn))

    # ===== 4. 改名级联台账 =====
    conn.execute(
        "INSERT INTO expense_ledger(period, subject1, subject2, book_amount) "
        "VALUES('2025-01','资产类','办公费',100)")
    conn.commit()
    asub.rename(c1, "办公费用", conn=conn)
    check("二级改名生效",
          [c["name"] for c in asub.get_tree(conn)[0]["children"]] == ["办公费用", "差旅费"])
    check("台账二级同步改名",
          conn.execute("SELECT COUNT(*) AS n FROM expense_ledger WHERE subject2='办公费用'")
          .fetchone()["n"] == 1)
    asub.rename(p1, "资产类别", conn=conn)
    check("一级改名生效", asub.get_tree(conn)[0]["name"] == "资产类别")
    check("台账一级同步改名",
          conn.execute("SELECT COUNT(*) AS n FROM expense_ledger WHERE subject1='资产类别'")
          .fetchone()["n"] == 1)
    expect_err("改名撞名", lambda: asub.rename(p2, "资产类别", conn=conn))

    # ===== 5. 删除级联 =====
    asub.delete(p2, conn=conn)  # 删除「费用类」应连带删除其下「招待费」
    check("一级删除后不在树", [p["name"] for p in asub.get_tree(conn)] == ["资产类别"])
    check("二级随父删除", len(asub.get_tree(conn)[0]["children"]) == 2)
    # 历史台账不动
    check("历史台账保留原科目文字",
          conn.execute("SELECT COUNT(*) AS n FROM expense_ledger WHERE subject1='资产类别'")
          .fetchone()["n"] == 1)

    # ===== 6. 排序：一级上移/下移 =====
    a = asub.add_level1("A", conn=conn)
    b = asub.add_level1("B", conn=conn)
    check("新增一级顺序末尾", [p["name"] for p in asub.get_tree(conn)] == ["资产类别", "A", "B"])
    asub.move_level1(a, -1, conn=conn)  # A 上移到资产类别之前
    check("一级上移", [p["name"] for p in asub.get_tree(conn)] == ["A", "资产类别", "B"])
    check("已在首位不能再上移", asub.move_level1(a, -1, conn=conn) is False)
    check("已在末位不能再下移", asub.move_level1(b, 1, conn=conn) is False)

    # ===== 7. 排序：二级上移/下移 =====
    ca = asub.add_level2(a, "a1", conn=conn)
    cb = asub.add_level2(a, "a2", conn=conn)
    cc = asub.add_level2(a, "a3", conn=conn)

    def a_children():
        for p in asub.get_tree(conn):
            if p["id"] == a:
                return [c["name"] for c in p["children"]]
        return []

    def kids_of(tree, pid):
        for p in tree:
            if p["id"] == pid:
                return p["children"]
        return []

    asub.move_level2(cb, -1, conn=conn)  # a2 上移 → a2,a1,a3
    check("二级上移结果", a_children() == ["a2", "a1", "a3"], f"got={a_children()}")
    asub.move_level2(cb, 1, conn=conn)  # a2 下移 → a1,a2,a3
    check("二级下移结果", a_children() == ["a1", "a2", "a3"], f"got={a_children()}")
    check("二级越界无效", asub.move_level2(cc, 1, conn=conn) is False)

    # ===== 8. 跨父搬运（reparent）不重复 =====
    b1 = asub.add_level2(b, "b1", conn=conn)
    asub.reparent_level2(ca, b, 0, conn=conn)  # 把 A 下的 a1 搬到 B 首位
    tree = asub.get_tree(conn)
    a_kids = [c["name"] for c in kids_of(tree, a)]
    b_kids = [c["name"] for c in kids_of(tree, b)]
    check("a1 移出 A", "a1" not in a_kids, f"got={a_kids}")
    check("a1 移入 B 首位", b_kids[0] == "a1", f"got={b_kids}")
    # 跨父搬运不得产生重复：a1 在整棵树里只应出现一次
    all_l2 = [c["name"] for p in tree for c in p["children"]]
    check("跨父搬运不重复(a1仅一次)", all_l2.count("a1") == 1, f"got={all_l2}")

    # ===== 9. save_layout_by_ids（id 定位，跨父不新建） =====
    tree = asub.get_tree(conn)
    a_kids_ids = [c["id"] for c in kids_of(tree, a)]
    b_kids_ids = [c["id"] for c in kids_of(tree, b)]
    # 把所有二级都放到 A 下（含原本在 B 的 a1/b1）
    asub.save_layout_by_ids(
        [{"id": a, "children": a_kids_ids + b_kids_ids}, {"id": b, "children": []}], conn=conn)
    tree = asub.get_tree(conn)
    a_kids = [c["name"] for c in kids_of(tree, a)]
    b_kids = [c["name"] for c in kids_of(tree, b)]
    check("save_layout 后 A 含全部二级", set(a_kids) == {"a2", "a3", "a1", "b1"}, f"got={a_kids}")
    check("save_layout 后 B 为空", b_kids == [])
    # 不重复：A 恰好 4 个二级（资产类别的 2 个历史二级不受影响）
    check("save_layout 不重复(A恰好4个二级)", len(kids_of(tree, a)) == 4, f"got={a_kids}")
    check("资产类别历史二级不受影响", set(c["name"] for c in kids_of(tree, p1)) == {"办公费用", "差旅费"})

    # ===== 10. 导入校验：未知组合 =====
    conn.execute("DELETE FROM account_subject")
    conn.commit()
    inc = asub.add_level1("收入", conn=conn)
    exp = asub.add_level1("支出", conn=conn)
    asub.add_level2(exp, "餐饮", conn=conn)
    rows = [
        {"subject1": "收入", "subject2": ""},
        {"subject1": "支出", "subject2": "餐饮"},
        {"subject1": "支出", "subject2": "住宿"},   # 未知二级
        {"subject1": "其他", "subject2": ""},         # 未知一级
    ]
    bad = asub.validate_ledger_subjects(rows, conn=conn)
    check("校验发现未知组合", bad == [("其他", ""), ("支出", "住宿")], f"got={bad}")
    check("已知组合通过", asub.validate_ledger_subjects(
        [{"subject1": "收入", "subject2": ""}, {"subject1": "支出", "subject2": "餐饮"}], conn=conn) == [])

    # ===== 11. 主表导入/导出整表替换 =====
    conn.execute("DELETE FROM account_subject")
    conn.commit()
    x = asub.add_level1("一级甲", conn=conn)
    asub.add_level2(x, "二级甲", conn=conn)
    exported = asub.export_rows(conn=conn)
    check("导出含一级+二级", len(exported) == 2 and exported[1]["level2"] == "二级甲", f"got={exported}")

    new_rows = [
        {"level1": "X", "level2": "", "code": "x1", "note": "n"},
        {"level1": "X", "level2": "x-a", "code": "", "note": ""},
        {"level1": "Y", "level2": "y-a", "code": "", "note": ""},
    ]
    asub.import_rows(new_rows, conn=conn)
    tree = asub.get_tree(conn)
    check("整表替换后只剩新数据", [p["name"] for p in tree] == ["X", "Y"], f"got={[p['name'] for p in tree]}")
    check("X 含 x-a", [c["name"] for c in tree[0]["children"]] == ["x-a"])

    print(f"\n通过 {OK} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  ✗", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
