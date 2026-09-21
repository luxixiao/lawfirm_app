"""账面情况聚合（app.engine.book_balance.load_pivot）单元测试 — 内存库。

运行：python tests/test_book_balance.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import account_subject as asub  # noqa: E402
from app.engine import book_balance as bb  # noqa: E402

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


def main() -> int:
    conn = make_conn()

    # 会计科目主表（树序：资产类 → 费用类）
    p1 = asub.add_level1("资产类", conn=conn)
    asub.add_level2(p1, "办公费", conn=conn)
    asub.add_level2(p1, "差旅费", conn=conn)
    p2 = asub.add_level1("费用类", conn=conn)
    asub.add_level2(p2, "招待费", conn=conn)

    # 费用台账：book_amount 按 period + subject1 + subject2
    rows = [
        ("2025-01", "资产类", "办公费", 100),
        ("2025-01", "资产类", "差旅费", 50),
        ("2025-02", "资产类", "办公费", 20),
        ("2025-01", "费用类", "招待费", 200),
        ("2025-03", "费用类", "招待费", 30),
        ("2025-01", "孤儿类", "子", 999),   # 主表未配置（孤儿一级）
        ("2025-01", "资产类", "", 5),        # 已知一级下的未分类金额
    ]
    conn.executemany(
        "INSERT INTO expense_ledger(period, subject1, subject2, book_amount) VALUES(?,?,?,?)",
        rows)
    conn.commit()

    # ===== 默认全年（1~12 月） =====
    res = bb.load_pivot(conn=conn, year="2025", start=1, end=12)
    r = res["rows"]
    check("行数=9（3一级+4二级明细+1未分类+1孤儿子+合计）", len(r) == 9, f"got={len(r)}")
    check("月份列=12", res["months"] == list(range(1, 13)), f"got={res['months']}")
    check("一级分组数=3", res["n_groups"] == 3, f"got={res['n_groups']}")

    # 资产类（加粗小计）
    check("资产类小计行", r[0]["name"] == "资产类" and r[0]["bold"] is True)
    check("资产类小计值", r[0]["vals"] == [155, 20, 0] + [0] * 9 and r[0]["total"] == 175,
          f"got={r[0]['vals']} total={r[0]['total']}")
    # s1/s2/kind 下钻字段（l1 一级小计）
    check("资产类 s1/s2/kind", r[0]["s1"] == "资产类" and r[0]["s2"] == "" and r[0]["kind"] == "l1",
          f"got={r[0]}")
    # 资产类下二级（缩进）
    check("办公费行", r[1]["name"] == "    办公费" and r[1]["vals"][:3] == [100, 20, 0] and r[1]["total"] == 120,
          f"got={r[1]}")
    check("办公费 s1/s2/kind(l2)", r[1]["s1"] == "资产类" and r[1]["s2"] == "办公费" and r[1]["kind"] == "l2",
          f"got={r[1]}")
    check("差旅费行", r[2]["name"] == "    差旅费" and r[2]["vals"][:3] == [50, 0, 0] and r[2]["total"] == 50,
          f"got={r[2]}")
    check("差旅费 s1/s2/kind(l2)", r[2]["s1"] == "资产类" and r[2]["s2"] == "差旅费" and r[2]["kind"] == "l2",
          f"got={r[2]}")
    check("未分类行", r[3]["name"] == "    (未分类)" and r[3]["vals"][:3] == [5, 0, 0] and r[3]["total"] == 5,
          f"got={r[3]}")
    check("未分类 s1/s2/kind(uncat)", r[3]["s1"] == "资产类" and r[3]["s2"] == "" and r[3]["kind"] == "uncat",
          f"got={r[3]}")

    # 费用类（加粗小计）
    check("费用类小计行", r[4]["name"] == "费用类" and r[4]["bold"] is True)
    check("费用类小计值", r[4]["vals"][:3] == [200, 0, 30] and r[4]["total"] == 230,
          f"got={r[4]['vals']} total={r[4]['total']}")
    check("费用类 s1/s2/kind(l1)", r[4]["s1"] == "费用类" and r[4]["s2"] == "" and r[4]["kind"] == "l1",
          f"got={r[4]}")
    check("招待费行", r[5]["name"] == "    招待费" and r[5]["total"] == 230)
    check("招待费 s1/s2/kind(l2)", r[5]["s1"] == "费用类" and r[5]["s2"] == "招待费" and r[5]["kind"] == "l2",
          f"got={r[5]}")

    # 孤儿类（主表未配置，置于末尾、按名序）
    check("孤儿类行", r[6]["name"] == "孤儿类" and r[6]["bold"] is True and r[6]["total"] == 999,
          f"got={r[6]}")
    check("孤儿类 s1/s2/kind(l1)", r[6]["s1"] == "孤儿类" and r[6]["s2"] == "" and r[6]["kind"] == "l1",
          f"got={r[6]}")
    check("孤儿子类行", r[7]["name"] == "    子" and r[7]["total"] == 999)
    check("孤儿子 s1/s2/kind(l2)", r[7]["s1"] == "孤儿类" and r[7]["s2"] == "子" and r[7]["kind"] == "l2",
          f"got={r[7]}")

    # 合计行
    check("合计行标记", r[8]["name"] == "合计" and r[8].get("grand") is True)
    check("合计逐月", r[8]["vals"][:3] == [1354, 20, 30], f"got={r[8]['vals']}")
    check("合计总额", r[8]["total"] == 1404, f"got={r[8]['total']}")
    check("合计 s1/s2/kind(grand)", r[8]["s1"] == "" and r[8]["s2"] == "" and r[8]["kind"] == "grand",
          f"got={r[8]}")

    # ===== 区间筛选 2025-01 ~ 2025-02 =====
    res2 = bb.load_pivot(conn=conn, year="2025", start=1, end=2)
    check("区间月份=[1,2]", res2["months"] == [1, 2], f"got={res2['months']}")
    r2 = res2["rows"]
    check("区间：资产类小计仍=175", r2[0]["total"] == 175, f"got={r2[0]['total']}")
    check("区间：合计逐月=[1354,20]", r2[-1]["vals"] == [1354, 20], f"got={r2[-1]['vals']}")
    # 区间过滤后，3 月招待费 30 不在范围内 → 合计=175+200+999=1374
    check("区间：合计总额=1374（不含3月）", r2[-1]["total"] == 1374, f"got={r2[-1]['total']}")

    # ===== 默认年份（空串→最新年份） =====
    res3 = bb.load_pivot(conn=conn, year="", start=1, end=12)
    check("默认年份取最新=2025", res3["year"] == "2025", f"got={res3['year']}")

    # ===== 空数据不崩 =====
    conn2 = make_conn()
    res4 = bb.load_pivot(conn=conn2, year="2025", start=1, end=12)
    check("空台账返回空行", res4["rows"] == [] and res4["n_groups"] == 0, f"got={res4}")

    # ===== expense_rows_for_cell 四种 kind 的 WHERE 正确性 =====
    conn3 = make_conn()
    p1 = asub.add_level1("资产类", conn=conn3)
    asub.add_level2(p1, "办公费", conn=conn3)
    p2 = asub.add_level1("费用类", conn=conn3)
    asub.add_level2(p2, "招待费", conn=conn3)
    seed = [
        # id, period, subject1, subject2, book_amount
        (1, "2025-01", "资产类", "办公费", 100),
        (2, "2025-01", "资产类", "差旅费", 50),
        (3, "2025-02", "资产类", "办公费", 20),
        (4, "2025-01", "资产类", "", 5),          # 未分类（subject2 空）
        (5, "2025-03", "费用类", "招待费", 30),
        (6, "2025-01", "孤儿类", "子", 999),        # 主表未配置（无 kind=l1 命中）
    ]
    conn3.executemany(
        "INSERT INTO expense_ledger(id, period, subject1, subject2, book_amount) VALUES(?,?,?,?,?)",
        seed)
    conn3.commit()
    months = [1, 2, 3]

    # l2：subject1 + subject2 精确命中
    l2 = bb.expense_rows_for_cell("2025", months, "资产类", "办公费", "l2", conn=conn3)
    check("l2 命中 subject2", sorted(x["id"] for x in l2) == [1, 3], f"got={[x['id'] for x in l2]}")
    # uncat：subject1 命中且 subject2 为空
    uncat = bb.expense_rows_for_cell("2025", months, "资产类", "", "uncat", conn=conn3)
    check("uncat 命中空 subject2", sorted(x["id"] for x in uncat) == [4], f"got={[x['id'] for x in uncat]}")
    # l1：仅按 subject1（含其下二级与空 subject2）
    l1 = bb.expense_rows_for_cell("2025", months, "资产类", "", "l1", conn=conn3)
    check("l1 仅按 subject1", sorted(x["id"] for x in l1) == [1, 2, 3, 4], f"got={[x['id'] for x in l1]}")
    # grand：全账期（忽略 subject 过滤）
    grand = bb.expense_rows_for_cell("2025", months, "", "", "grand", conn=conn3)
    check("grand 全账期", sorted(x["id"] for x in grand) == [1, 2, 3, 4, 5, 6],
          f"got={[x['id'] for x in grand]}")
    # 区间过滤：仅 1~2 月，l1 命中资产类应为 id 1/2/3/4 中 period<=2025-02 → 1/2/3/4? 4 是 2025-01 命中
    l1b = bb.expense_rows_for_cell("2025", [1, 2], "资产类", "", "l1", conn=conn3)
    check("l1 区间[1,2]过滤", sorted(x["id"] for x in l1b) == [1, 2, 3, 4], f"got={[x['id'] for x in l1b]}")

    print(f"\n通过 {OK} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  ✗", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
