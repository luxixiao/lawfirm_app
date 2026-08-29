"""个税申报数据引擎（tax_declaration）

- 数据来自每年 11 月税局导出的「1-11 月累计」申报表，一年一份，按 year 区分。
- 字段按税局**字段编号**映射（编号体系稳定），未知/新增列存在 extra_json 中，
  由页面动态追加为附加列展示——税局表格增列减列都不会丢数据、也不会导入失败。
"""
from __future__ import annotations

import json
from typing import Dict, List

from app.db import get_conn


def year_options() -> List[str]:
    """已导入的申报年份（降序）。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT year FROM tax_declaration WHERE year != ''"
        ).fetchall()
    finally:
        conn.close()
    return sorted({r["year"] for r in rows}, reverse=True)


def list_rows(year: str = "", keyword: str = "") -> List[Dict]:
    """查询申报行（按年份 / 姓名过滤）。"""
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
        # 按 id（= 插入顺序 = 源文件行序）排列，最大程度还原源文件；
        # 需要按序号排序时用页面表头排序即可（源表存在序号空缺的行，故不按 seq 排）。
        rows = conn.execute(
            f"SELECT * FROM tax_declaration{where} ORDER BY year, id",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def extra_columns(year: str = "") -> List[str]:
    """该年份数据中出现过的附加列名（税局新增列），供页面动态追加列。"""
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
    """覆盖式保存某年申报数据（同年重导先清空旧数据）。返回写入行数。"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM tax_declaration WHERE year=?", (year,))
        for it in items:
            conn.execute(
                """INSERT INTO tax_declaration
                   (year, seq, staff_name, income, basic_deduction, special_deduction,
                    child_edu, elderly, housing_loan, housing_rent, education, infant,
                    taxable, tax_rate, quick_ded, payable, relief, paid, refill, net_paid,
                    extra_json, file_name)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (year, it.get("seq") or "", it.get("staff_name") or "",
                 it.get("income") or 0.0, it.get("basic_deduction") or 0.0,
                 it.get("special_deduction") or 0.0, it.get("child_edu") or 0.0,
                 it.get("elderly") or 0.0, it.get("housing_loan") or 0.0,
                 it.get("housing_rent") or 0.0, it.get("education") or 0.0,
                 it.get("infant") or 0.0, it.get("taxable") or 0.0,
                 it.get("tax_rate") or "", it.get("quick_ded") or 0.0,
                 it.get("payable") or 0.0, it.get("relief") or 0.0,
                 it.get("paid") or 0.0, it.get("refill") or 0.0,
                 it.get("net_paid") or 0.0, it.get("extra_json") or "", file_name),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(items)


def delete_year(year: str) -> None:
    """删除某年的申报数据。"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM tax_declaration WHERE year=?", (year,))
        conn.commit()
    finally:
        conn.close()
