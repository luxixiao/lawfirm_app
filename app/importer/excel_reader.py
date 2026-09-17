"""通用 Excel 读取器：支持 .xls/.xlsx/.xlsm"""
from __future__ import annotations

from pathlib import Path
from typing import List

import xlrd
from app.importer.xlsx_io import load_workbook


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

    wb = load_workbook(path, data_only=True)
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
    wb = load_workbook(path, read_only=True)
    try:
        return wb.sheetnames
    finally:
        wb.close()


def norm_header(s) -> str:
    """表头文本规范化：**抹掉全部空白**（半角空格 / 全角空格 U+3000 / Tab / 换行）。

    为什么需要：台账 Excel 的表头常带排版空格 —— 真台账 4 个 sheet 的对方列表头
    实际是「对  方」（中间两个空格），不是「对方」；按原文比较会整列取不到值
    （曾导致 raw_ledger.buyer 93/93 全空、「发票台账」页对方列空白）。
    所有「按表头名找列」的地方一律走本函数，口径统一。
    """
    return "".join(str(s or "").split())


def find_header_row(rows: List[List[str]], required: List[str]) -> int:
    """定位表头行（须同时包含 required 中的关键词，忽略空白），找不到返回 -1"""
    for i, row in enumerate(rows):
        joined = "".join(norm_header(c) for c in row)
        if all(norm_header(k) in joined for k in required):
            return i
    return -1


def col_index(header: List[str], *keywords: str) -> int:
    """在表头中找列下标（忽略空白，返回第一个命中关键词的列），找不到返回 -1"""
    for i, h in enumerate(header):
        hh = norm_header(h)
        if any(norm_header(k) in hh for k in keywords):
            return i
    return -1
