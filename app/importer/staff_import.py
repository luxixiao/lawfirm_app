"""职工花名册导入解析（模板 = 职工清单.xlsx：姓名|类型|备注）"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Tuple

import xlrd
from app.importer.xlsx_io import load_workbook


class ImportError_(Exception):
    """导入异常（带用户可读信息）"""


def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def _find_header_row(rows: List[List[str]]) -> int:
    """定位表头行：包含'姓名'且包含'类型'"""
    for i, row in enumerate(rows):
        names = [_cell_text(c) for c in row]
        if any("姓名" in n for n in names) and any("类型" in n for n in names):
            return i
    return -1


def parse_staff_file(path: str) -> Tuple[List[Tuple[str, str, str]], str]:
    """解析职工清单文件，返回 ([(name, staff_type, note)...], file_hash)"""
    p = Path(path)
    if not p.exists():
        raise ImportError_(f"文件不存在: {path}")
    suffix = p.suffix.lower()
    rows: List[List[str]] = []

    if suffix == ".xlsx":
        wb = load_workbook(path, data_only=True)
        ws = wb[wb.sheetnames[0]]
        for row in ws.iter_rows(values_only=True):
            rows.append([_cell_text(c) for c in row])
        wb.close()
    elif suffix == ".xls":
        wb = xlrd.open_workbook(path)
        ws = wb.sheet_by_index(0)
        for i in range(ws.nrows):
            rows.append([_cell_text(ws.cell_value(i, j)) for j in range(ws.ncols)])
    else:
        raise ImportError_(f"不支持的文件格式: {suffix}（支持 .xls/.xlsx/.xlsm）")

    hr = _find_header_row(rows)
    if hr < 0:
        raise ImportError_("未找到表头（需包含'姓名'和'类型'列）")

    header = rows[hr]
    idx_name = next(i for i, h in enumerate(header) if "姓名" in h)
    idx_type = next(i for i, h in enumerate(header) if "类型" in h)
    idx_note = next((i for i, h in enumerate(header) if "备注" in h), -1)

    staff: List[Tuple[str, str, str]] = []
    for row in rows[hr + 1:]:
        name = _cell_text(row[idx_name]) if idx_name < len(row) else ""
        if not name:
            continue
        stype = _cell_text(row[idx_type]) if idx_type < len(row) else ""
        note = _cell_text(row[idx_note]) if idx_note >= 0 and idx_note < len(row) else ""
        staff.append((name, stype, note))

    if not staff:
        raise ImportError_("文件中没有有效的职工数据")

    file_hash = hashlib.md5(p.read_bytes()).hexdigest()
    return staff, file_hash
