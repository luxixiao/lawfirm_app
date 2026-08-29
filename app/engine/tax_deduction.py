"""费用扣除数据引擎（tax_deduction）

- 数据来自每年税局导出的「1-12 月」个税费用扣除表，一年一份，按 year 区分与覆盖。
- 该表没有税局字段编号行，字段以**列名关键词**匹配；未知/新增列存在 extra_json 中，
  由页面动态追加为附加列——税局表格增列/减列/改名都不影响导入与查看。
"""
from __future__ import annotations

import json
from typing import Dict, List

from app.db import get_conn


def year_options() -> List[str]:
    """已导入的年份（降序）。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT year FROM tax_deduction WHERE year != ''"
        ).fetchall()
    finally:
        conn.close()
    return sorted({r["year"] for r in rows}, reverse=True)


def list_rows(year: str = "", keyword: str = "") -> List[Dict]:
    """查询费用扣除行（按年份 / 姓名过滤）。"""
    clauses, params = [], []
    if year:
        clauses.append("year = ?")
        params.append(year)
    if keyword:
        clauses.append("staff_name LIKE ?")
        params.append(f"%{keyword}%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_conn()
    try:
        # 按 id（= 插入顺序 = 源文件行序）排列，最大程度还原源文件
        rows = conn.execute(
            f"SELECT * FROM tax_deduction{where} ORDER BY year, id",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def extra_columns(year: str = "") -> List[str]:
    """该年份数据中出现过的附加列名（税局新增列 / 重名列），供页面动态追加列。"""
    cols: List[str] = []
    for r in list_rows(year=year):
        try:
            data = json.loads(r.get("extra_json") or "{}")
        except (TypeError, ValueError):
            continue
        for k in data:
            if k not in cols:
                cols.append(k)
    return cols


def save_year(year: str, items: List[Dict], file_name: str = "") -> int:
    """覆盖式保存某年费用扣除数据（同年重导先清空旧数据）。返回写入行数。"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM tax_deduction WHERE year=?", (year,))
        for it in items:
            conn.execute(
                """INSERT INTO tax_deduction
                   (year, staff_name, basic_deduction, special_deduction, additional_total,
                    child_edu, education, housing_loan, housing_rent, elderly, infant,
                    pension, other_deduction, donation, other_income, other_deduct,
                    other_relief, extra_json, file_name)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (year, it.get("staff_name") or "",
                 it.get("basic_deduction") or 0.0, it.get("special_deduction") or 0.0,
                 it.get("additional_total") or 0.0, it.get("child_edu") or 0.0,
                 it.get("education") or 0.0, it.get("housing_loan") or 0.0,
                 it.get("housing_rent") or 0.0, it.get("elderly") or 0.0,
                 it.get("infant") or 0.0, it.get("pension") or 0.0,
                 it.get("other_deduction") or 0.0, it.get("donation") or 0.0,
                 it.get("other_income") or 0.0, it.get("other_deduct") or 0.0,
                 it.get("other_relief") or 0.0, it.get("extra_json") or "", file_name),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(items)


def delete_year(year: str) -> None:
    """删除某年的费用扣除数据。"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM tax_deduction WHERE year=?", (year,))
        conn.commit()
    finally:
        conn.close()
