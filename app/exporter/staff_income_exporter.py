"""年度业务收入结算表（聘用律师）导出器

仿模板「2025年度业务收入结算表（聘用律师）.xls」：
- 每个月份一个 sheet（202501~YYYYMM，截至当前月）
- 列：序号 | 姓名 | 本年收入(本月/累计) | 报酬发放(本月/累计) | 住房公积金(本月/累计)
     | 保险费(本月/累计) | 汽油费(本月/累计) | 合计行
- 本年收入 = 业务收入（聘用 = 收款净额）
- 报酬发放/住房公积金/保险费/汽油费 = 按「费用归类」维护映射汇总费用类型
- 名单 = 员工清单「聘用」类型且在职
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import openpyxl
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties

from app.db import get_conn
from app.engine.expense_cat import get_by_category, get_map
from app.engine.person_settlement import build_settlement

THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BOLD = Font(bold=True)
CENTER = Alignment(horizontal="center", vertical="center")
RIGHT = Alignment(horizontal="right", vertical="center")

# 列 → 归类
COLS = [("报酬发放", "报酬发放"), ("住房公积金", "住房公积金"), ("保险费", "保险费"), ("汽油费", "汽油费")]


def _staff_employees(conn, year: int, month: int) -> list:
    """聘用/兼职（视同聘用）且在职的员工；入职月份晚于当前月则排除（按姓名）"""
    # 离职不影响：不按 is_active 过滤，只要该月有数据/在名单即纳入（hire_month 过滤入职）
    rows = conn.execute(
        "SELECT name, hire_month FROM staff "
        "WHERE staff_type LIKE '%聘用%' OR staff_type LIKE '%兼职%' ORDER BY name"
    ).fetchall()
    cur = f"{year}-{month:02d}"
    out = []
    for r in rows:
        hm = (r["hire_month"] or "").strip()
        if hm and hm > cur:
            continue  # 尚未入职
        out.append(r["name"])
    return out


def _build_rows(persons: list, data: Dict, cat_map: Dict, year: int, month: int):
    """某月年度聘用结算表的行数据（预览/导出共用）

    rows: [[姓名, 收入本月, 收入累计, 报酬本月, 报酬累计, 公积金本月, 公积金累计,
            保险本月, 保险累计, 汽油本月, 汽油累计], ...]
    totals: 各数值列合计（10 个）
    """
    rows = []
    for name in persons:
        st = data.get(name)
        if st is None:
            rows.append([name] + [0.0] * 10)
            continue
        m = st["months"]
        income_cur = m[month]["income"]
        income_tot = round(sum(m[mo]["income"] for mo in range(1, month + 1)), 2)
        fees = []
        for _label, cat in COLS:
            types = [t for t in cat_map if cat_map[t] == cat]
            cur = _exp_sum(st, month, types)
            tot = round(sum(_exp_sum(st, mo, types) for mo in range(1, month + 1)), 2)
            fees += [cur, tot]
        rows.append([name, income_cur, income_tot] + fees)
    totals = [round(sum(r[j] for r in rows), 2) for j in range(1, 11)]
    return rows, totals


def build_report_rows(year: int, month: int):
    """预览/导出共用：返回 (persons, rows, totals)"""
    data = build_settlement(year)
    cat_map = get_map()
    conn = get_conn()
    try:
        persons = _staff_employees(conn, year, month)
    finally:
        conn.close()
    rows, totals = _build_rows(persons, data, cat_map, year, month)
    return persons, rows, totals


def _write_sheet(ws, year: int, month: int, persons: list, data: Dict, cat_map: Dict) -> None:
    ws.merge_cells("A1:L1")
    ws["A1"] = "浙江震天律师事务所"
    ws["A1"].font = Font(size=13, bold=True)
    ws["A1"].alignment = CENTER
    ws.merge_cells("A2:L2")
    ws["A2"] = f"聘用律师{year}年{month:02d}月业务收入结算表"
    ws["A2"].alignment = CENTER

    # 表头（合并：A 序号 B 姓名 C:D 本年收入 E:F 报酬发放 G:H 住房公积金 I:J 保险费 K:L 汽油费）
    heads = [(3, 1, "序号"), (3, 2, "姓名")]
    col_idx = 3
    for label in ["本年收入", "报酬发放", "住房公积金", "保险费", "汽油费"]:
        ws.merge_cells(start_row=3, start_column=col_idx, end_row=3, end_column=col_idx + 1)
        ws.cell(3, col_idx, label)
        ws.cell(4, col_idx, "本月")
        ws.cell(4, col_idx + 1, "累计")
        col_idx += 2
    for j in range(1, 13):
        ws.cell(3, j).font = BOLD
        ws.cell(4, j).font = BOLD
        ws.cell(3, j).alignment = CENTER
        ws.cell(4, j).alignment = CENTER
        ws.cell(3, j).border = BORDER
        ws.cell(4, j).border = BORDER

    rows, totals = _build_rows(persons, data, cat_map, year, month)
    r = 5
    for i, row in enumerate(rows, 1):
        ws.cell(r, 1, i).alignment = CENTER
        ws.cell(r, 2, row[0]).alignment = Alignment(horizontal="left", vertical="center")
        for j in range(3, 13):
            ws.cell(r, j, row[j - 2])
        for j in range(1, 13):
            cell = ws.cell(r, j)
            if j >= 3:
                cell.number_format = "#,##0.00"
                cell.alignment = RIGHT
            cell.border = BORDER
        r += 1

    # 合计
    ws.cell(r, 2, "合计").font = BOLD
    for j in range(3, 13):
        c = ws.cell(r, j, round(totals[j - 3], 2))
        c.number_format = "#,##0.00"
        c.alignment = RIGHT
        c.font = BOLD
    ws.cell(r, 1).border = BORDER
    for j in range(2, 13):
        ws.cell(r, j).border = BORDER

    # 列宽 + 打印一页
    widths = [6, 10, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12]
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_margins = PageMargins(left=0.25, right=0.25, top=0.3, bottom=0.3)


def _exp_sum(st: Dict, month: int, types: list) -> float:
    exp = st["expenses"]
    return round(sum(exp.get(t, {}).get(month, 0.0) for t in types), 2)


def export_staff_income(out_path: str | Path, year: int, month_to: int) -> Path:
    """生成年度聘用律师业务收入结算表（1~month_to 各一个 sheet）"""
    data = build_settlement(year)
    cat_map = get_map()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for month in range(1, month_to + 1):
        conn = get_conn()
        try:
            persons = _staff_employees(conn, year, month)
        finally:
            conn.close()
        ws = wb.create_sheet(title=f"{year}{month:02d}")
        _write_sheet(ws, year, month, persons, data, cat_map)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
