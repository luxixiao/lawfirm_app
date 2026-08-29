"""工资累计引擎

- 数据源：raw_salary（工资文档镜像）+ import_batch（账期 period，来自文件名）。
- 按「姓名 + 类别」聚合**跨月**累计数：月数 / 每月工资 / 代扣个所税 / 代扣公积金 / 实发金额。
- 月数按**去重后的账期数**统计（同一人同月若同时有「分成报酬」与「工资」两条，仍算 1 个月）。
- 金额为各月数值求和（同一月的多条记录都计入，不丢钱）。
- 与工资表「还原页」解耦：本模块只做汇总统计，不改动任何原始镜像数据。
"""
from __future__ import annotations

from typing import Dict, List

from app.db import get_conn

# 应发金额：三个金额列每行只有一个非零，直接相加即可覆盖全部类别
GROSS_EXPR = "(COALESCE(s.share_num,0) + COALESCE(s.salary_num,0) + COALESCE(s.partner_num,0))"


def year_options() -> List[str]:
    """返回库中存在的所有账期年份（降序），供页面年份下拉使用。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT substr(b.period,1,4) AS y "
            "FROM raw_salary s LEFT JOIN import_batch b ON b.id = s.import_batch_id "
            "WHERE b.period IS NOT NULL AND b.period != ''"
        ).fetchall()
    finally:
        conn.close()
    return sorted({r["y"] for r in rows if r["y"]}, reverse=True)


def summary_rows(year: str = "", sheet_key: str = "", keyword: str = "") -> List[Dict]:
    """按 姓名 + 类别 聚合的跨月累计行。

    year:      ""=全部年份；否则按账期年份过滤（如 "2025"）。
    sheet_key: ""=全部类别；否则 lawyer / partner / logistics。
    keyword:   姓名模糊匹配。
    """
    clauses, params = [], []
    if year:
        clauses.append("substr(b.period,1,4) = ?")
        params.append(year)
    if sheet_key:
        clauses.append("s.sheet_key = ?")
        params.append(sheet_key)
    if keyword:
        clauses.append("s.staff_name LIKE ?")
        params.append(f"%{keyword}%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT s.staff_name AS staff_name,
               s.sheet_key  AS sheet_key,
               COUNT(DISTINCT b.period)   AS months,
               SUM({GROSS_EXPR})          AS gross,
               SUM(COALESCE(s.tax_num,0)) AS tax,
               SUM(COALESCE(s.fund_num,0))AS fund,
               SUM(COALESCE(s.net_num,0)) AS net
        FROM raw_salary s
        LEFT JOIN import_batch b ON b.id = s.import_batch_id
        {where}
        GROUP BY s.staff_name, s.sheet_key
        ORDER BY s.sheet_key, s.staff_name
    """
    conn = get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def detail_rows(staff_name: str, sheet_key: str = "", year: str = "") -> List[Dict]:
    """某人的逐月明细（供页面下钻查看：点某人看每个月分别是多少）。"""
    clauses = ["s.staff_name = ?"]
    params: list = [staff_name]
    if sheet_key:
        clauses.append("s.sheet_key = ?")
        params.append(sheet_key)
    if year:
        clauses.append("substr(b.period,1,4) = ?")
        params.append(year)
    where = " WHERE " + " AND ".join(clauses)
    sql = f"""
        SELECT b.period AS period, s.sheet_key AS sheet_key, s.item_type AS item_type,
               {GROSS_EXPR} AS gross,
               COALESCE(s.tax_num,0)  AS tax,
               COALESCE(s.fund_num,0) AS fund,
               COALESCE(s.net_num,0)  AS net
        FROM raw_salary s
        LEFT JOIN import_batch b ON b.id = s.import_batch_id
        {where}
        ORDER BY b.period, s.block_no, s.row_no
    """
    conn = get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
