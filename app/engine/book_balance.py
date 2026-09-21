"""账面情况聚合引擎（纯逻辑，与 UI 解耦，便于无头单测）。

产出「按账期汇总各会计科目账面费用金额」的透视结构：
- 行：会计科目（一级分组、二级缩进、顺序 = account_subject 的 sort_order）
- 列：选定月份的逐月值 + 末尾「总计」
- 每个一级科目有加粗小计行；底部有「合计」行（= 各一级小计之和，避免重复累加）
- 顺序：account_subject 树序在前；台账里出现但主表未配置的孤儿科目置于末尾、按名称排序
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.db import get_conn
from app.engine import account_subject as asub


def _own_conn(conn=None):
    return conn is None, conn if conn is not None else get_conn()


def _close(own: bool, conn) -> None:
    if own:
        conn.close()


def _pair_vals(pivot, s1: str, s2: str, months, year: str):
    vals = []
    tot = 0.0
    for m in months:
        p = f"{year}-{m:02d}"
        x = pivot.get((s1, s2), {}).get(p, 0.0)
        vals.append(x)
        tot += x
    return vals, tot


def _group_total(pivot, s1: str, display_s2, has_empty: bool, months, year: str):
    vals = [0.0] * len(months)
    tot = 0.0
    for s2 in display_s2 + ([""] if has_empty else []):
        v, t = _pair_vals(pivot, s1, s2, months, year)
        for i, x in enumerate(v):
            vals[i] += x
        tot += t
    return vals, tot


def load_pivot(conn=None, year: str = "", start: int = 1, end: int = 12) -> Dict:
    """返回透视结构 dict：

    {
      "year": str, "start": int, "end": int, "months": [int,...],
      "rows": [ {"name", "bold", "vals":[float], "total": float, "grand"?: True}, ... ],
      "n_groups": int,
    }
    - year 缺省时取台账中最新年份。
    - 金额来自 expense_ledger.book_amount，按 period + subject1 + subject2 聚合。
    """
    own, conn = _own_conn(conn)
    try:
        if not year:
            r = conn.execute(
                "SELECT MAX(substr(period,1,4)) FROM expense_ledger "
                "WHERE period LIKE '____-__'").fetchone()
            year = r[0] or ""
        if start > end:
            start, end = end, start
        months = list(range(start, end + 1))
        lo = f"{year}-{start:02d}"
        hi = f"{year}-{end:02d}"

        pivot: Dict[Tuple[str, str], Dict[str, float]] = {}
        for r in conn.execute(
            "SELECT subject1, subject2, period, COALESCE(SUM(book_amount),0) AS amt "
            "FROM expense_ledger WHERE period>=? AND period<=? "
            "GROUP BY subject1, subject2, period", (lo, hi)):
            s1 = r["subject1"] or ""
            s2 = r["subject2"] or ""
            pivot.setdefault((s1, s2), {})[r["period"]] = float(r["amt"])

        tree = asub.get_tree(conn)
        l1_order = [p["name"] for p in tree]
        l1_children = {p["name"]: [c["name"] for c in p["children"]] for p in tree}

        all_l1 = {a for (a, b) in pivot if a}
        master = [s1 for s1 in l1_order if s1 in all_l1]
        orphan = sorted(all_l1 - set(l1_order))

        rows: List[Dict] = []
        for s1 in master + orphan:
            tree_children = l1_children.get(s1, [])
            ledger_s2 = {b for (a, b) in pivot if a == s1 and b}
            display_s2 = [c for c in tree_children if c in ledger_s2]
            display_s2 += sorted(ledger_s2 - set(tree_children))
            has_empty = (s1, "") in pivot

            l1_vals, l1_tot = _group_total(pivot, s1, display_s2, has_empty, months, year)
            rows.append({"name": s1, "bold": True, "vals": l1_vals, "total": l1_tot,
                         "s1": s1, "s2": "", "kind": "l1"})
            for s2 in display_s2:
                v, t = _pair_vals(pivot, s1, s2, months, year)
                rows.append({"name": "    " + s2, "bold": False, "vals": v, "total": t,
                             "s1": s1, "s2": s2, "kind": "l2"})
            if has_empty:
                v, t = _pair_vals(pivot, s1, "", months, year)
                rows.append({"name": "    (未分类)", "bold": False, "vals": v, "total": t,
                             "s1": s1, "s2": "", "kind": "uncat"})

        gt_vals = [0.0] * len(months)
        gt = 0.0
        for s in rows:
            if s["bold"]:
                for i, x in enumerate(s["vals"]):
                    gt_vals[i] += x
                gt += s["total"]
        if rows:
            rows.append({"name": "合计", "bold": True, "vals": gt_vals, "total": gt,
                         "grand": True, "s1": "", "s2": "", "kind": "grand"})

        n_groups = sum(1 for s in rows if s["bold"] and not s.get("grand"))
        return {"year": year, "start": start, "end": end, "months": months,
                "rows": rows, "n_groups": n_groups}
    finally:
        _close(own, conn)


def expense_rows_for_cell(period: str, months: List[int], s1: str, s2: str,
                          kind: str, conn=None) -> List[dict]:
    """下钻：返回组成某透视单元格的全部 expense_ledger 行（全列，供详情页展示/编辑）。

    kind 分派（见 design plan §4.2 / C-2）：
    - 'l2'   → WHERE subject1=s1 AND subject2=s2 AND period IN months
    - 'uncat'→ WHERE subject1=s1 AND (subject2 IS NULL OR subject2='') AND period IN months
    - 'l1'   → WHERE subject1=s1 AND period IN months
    - 'grand'→ WHERE period IN months（其余 kind 同 grand）

    period 形如 '2025'，months 如 [1..12] 或 [1,2]；行取全部列。
    """
    own, c = _own_conn(conn)
    try:
        ym = [f"{period}-{m:02d}" for m in months]
        ph = ",".join("?" for _ in ym)
        if kind == "l2":
            sql, args = ("SELECT * FROM expense_ledger WHERE subject1=? AND subject2=? "
                         "AND period IN (" + ph + ")"), [s1, s2]
        elif kind == "uncat":
            sql, args = ("SELECT * FROM expense_ledger WHERE subject1=? "
                         "AND (subject2 IS NULL OR subject2='') AND period IN (" + ph + ")"), [s1]
        elif kind == "l1":
            sql, args = ("SELECT * FROM expense_ledger WHERE subject1=? "
                         "AND period IN (" + ph + ")"), [s1]
        else:  # grand
            sql, args = ("SELECT * FROM expense_ledger WHERE period IN (" + ph + ")"), []
        return [dict(r) for r in c.execute(sql, args + ym).fetchall()]
    finally:
        _close(own, c)
