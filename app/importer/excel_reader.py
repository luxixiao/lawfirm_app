"""通用 Excel 读取器：支持 .xls/.xlsx/.xlsm"""
from __future__ import annotations

from pathlib import Path
from typing import List

import openpyxl
import xlrd


class ImportError_(Exception):
    """导入异常（带用户可读信息）"""


def cell_text(v) -> str:
    """单元格值转文本（数字去 .0）"""
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def read_sheet(path: str, sheet_index: int = 0, sheet_name: str | None = None) -> List[List[str]]:
    """读取指定 sheet，返回文本二维列表"""
    p = Path(path)
    if not p.exists():
        raise ImportError_(f"文件不存在: {path}")
    suffix = p.suffix.lower()
    if suffix not in (".xls", ".xlsx", ".xlsm"):
        raise ImportError_(f"不支持的文件格式: {suffix}（支持 .xls/.xlsx/.xlsm）")

    if suffix == ".xls":
        wb = xlrd.open_workbook(path)
        ws = wb.sheet_by_name(sheet_name) if sheet_name else wb.sheet_by_index(sheet_index)
        return [[cell_text(ws.cell_value(i, j)) for j in range(ws.ncols)] for i in range(ws.nrows)]

    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        ws = wb[sheet_name] if sheet_name else wb.worksheets[sheet_index]
        return [[cell_text(c) for c in row] for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def sheet_names(path: str) -> List[str]:
    """返回所有 sheet 名"""
    suffix = Path(path).suffix.lower()
    if suffix == ".xls":
        return xlrd.open_workbook(path).sheet_names()
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        return wb.sheetnames
    finally:
        wb.close()


def find_header_row(rows: List[List[str]], required: List[str]) -> int:
    """定位表头行（须同时包含 required 中的关键词，忽略空格），找不到返回 -1"""
    for i, row in enumerate(rows):
        joined = "".join(row).replace(" ", "")
        if all(k.replace(" ", "") in joined for k in required):
            return i
    return -1


def col_index(header: List[str], *keywords: str) -> int:
    """在表头中找列下标（忽略空格，返回第一个命中关键词的列），找不到返回 -1"""
    for i, h in enumerate(header):
        hh = h.replace(" ", "")
        if any(k.replace(" ", "") in hh for k in keywords):
            return i
    return -1
