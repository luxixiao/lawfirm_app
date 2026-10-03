"""年度结算表导出器（原「年度业务收入结算表（聘用律师）」，去身份后不再区分人员类型）

- 每个月份一个 sheet（202501~YYYYMM，截至当前月）
- 列：序号 | 姓名 | 本年收入(本月/累计) | 报酬发放(本月/累计) | 住房公积金(本月/累计)
     | 保险费(本月/累计) | 汽油费(本月/累计) | 合计行
- 本年收入 = 业务收入（按各人类型的「业务金额方式」net_basis）
- 报酬发放/住房公积金/保险费/汽油费 = 按「费用归类」维护映射汇总费用类型
- 名单 = 任一类型勾了「开票」或「报销」的人员（与进报表口径一致，勾线即进）
- 展示三态：合并 / 仅多类型拆分 / 全拆分（按类型展开，同一人多类型分别显示），
  语义见 app/engine/person_settlement.report_entries
- persons 参数 = 工具栏勾选的人员（None=不过滤）；逐月按当月名单取交集
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
from app.engine.person_settlement import build_settlement, report_entries
from app.engine import staff_type
from app.exporter.report_persons import apply_person_filter, list_report_persons

THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BOLD = Font(bold=True)
CENTER = Alignment(horizontal="center", vertical="center")
RIGHT = Alignment(horizontal="right", vertical="center")

# 列 → 归类
COLS = [("报酬发放", "报酬发放"), ("住房公积金", "住房公积金"), ("保险费", "保险费"), ("汽油费", "汽油费")]


def _staff_employees(conn, year: int, month: int) -> list:
    """名单：任一类型勾了「开票」或「报销」的人员（勾线即进，不区分身份）。

    薄包装：实际口径已收敛到 report_persons.list_report_persons（两张列表式报表共用）。
    保留本函数名与签名，6 处内部调用点零改动。
    """
    return list_report_persons(conn, year, month)


def _build_rows(entries: list, cat_map: Dict, year: int, month: int):
    """某月年度结算表的行数据（预览/导出共用）。

    entries: [(display_name, st_or_None), ...]（合并/拆分统一，来自 report_entries）。
    rows: [[姓名, 收入本月, 收入累计, 报酬本月, 报酬累计, 公积金本月, 公积金累计,
            保险本月, 保险累计, 汽油本月, 汽油累计], ...]
    totals: 各数值列合计（10 个）
    """
    rows = []
    for display, st in entries:
        if st is None:
            rows.append([display] + [0.0] * 10)
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
        rows.append([display, income_cur, income_tot] + fees)
    totals = [round(sum(r[j] for r in rows), 2) for j in range(1, 11)]
    return rows, totals


def build_report_rows(year: int, month: int, split: "bool | str" = False, persons=None):
    """预览/导出共用：返回 (names, rows, totals)。split 传三态字符串或 bool（兼容旧调用）。"""
    cat_map = get_map()
    conn = get_conn()
    try:
        roster = _staff_employees(conn, year, month)
        types = staff_type.active_types(conn)
    finally:
        conn.close()
    selected = apply_person_filter(roster, persons)
    entries = report_entries(year, selected, split, types)
    rows, totals = _build_rows(entries, cat_map, year, month)
    names = [d for d, _ in entries]
    return names, rows, totals


def _write_sheet(ws, year: int, month: int, entries: list, cat_map: Dict) -> None:
    ws.merge_cells("A1:L1")
    ws["A1"] = "浙江震天律师事务所"
    ws["A1"].font = Font(size=13, bold=True)
    ws["A1"].alignment = CENTER
    ws.merge_cells("A2:L2")
    ws["A2"] = f"{year}年{month:02d}月业务收入结算表"
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

    rows, totals = _build_rows(entries, cat_map, year, month)
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


def export_staff_income(out_path: str | Path, year: int, month_to: int,
                         split: "bool | str" = False, persons=None) -> Path:
    """生成年度结算表模板表（1~month_to 各一个 sheet，从新到旧排序）。
    split 传三态字符串或 bool（兼容旧调用）。persons 为勾选人员，None=不过滤。

    ⚠️逐月取交集、不复用同一列表：hire_month 过滤随月份变化（张三 3 月入职 →
    1/2 月名单里天然没有他）。空交集的 sheet 仍照常生成（只剩表头 + 合计 0），
    sheet 名与数量只由年月决定，保证模板形态可跨次对比。
    """
    cat_map = get_map()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for month in range(month_to, 0, -1):
        conn = get_conn()
        try:
            roster_m = _staff_employees(conn, year, month)
            types = staff_type.active_types(conn)
        finally:
            conn.close()
        selected_m = apply_person_filter(roster_m, persons)
        entries = report_entries(year, selected_m, split, types)
        ws = wb.create_sheet(title=f"{year}{month:02d}")
        _write_sheet(ws, year, month, entries, cat_map)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def export_staff_income_month(out_path: str | Path, year: int, month: int,
                              split: "bool | str" = False, persons=None) -> Path:
    """生成单月年度结算表（单个 sheet，与预览一致）。split 传三态字符串或 bool。"""
    cat_map = get_map()
    conn = get_conn()
    try:
        roster = _staff_employees(conn, year, month)
        types = staff_type.active_types(conn)
    finally:
        conn.close()
    selected = apply_person_filter(roster, persons)
    entries = report_entries(year, selected, split, types)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{year}{month:02d}"
    _write_sheet(ws, year, month, entries, cat_map)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
