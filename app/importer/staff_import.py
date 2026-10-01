"""职工花名册导入解析。

模板（与 `staff_view.export_roster` 导出完全对称）：
    编号 | 姓名 | 身份证号 | 手机号 | 入职月份 | 离职月份 | 备注
「类型」列**可选**——纯花名册（导出格式）本就不含类型，有类型列的旧职工清单仍兼容。
编号 / 身份证 / 手机 / 入职 / 离职 / 备注 各列存在则读取、不存在则留空，绝不报错。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import xlrd
from app.importer.excel_reader import norm_header
from app.importer.xlsx_io import load_workbook


class ImportError_(Exception):
    """导入异常（带用户可读信息）"""


@dataclass
class RosterRow:
    """花名册一行：姓名必填，其余字段（含员工类型）均可选。

    字段顺序与 `staff_roster` 表列对齐：name/code/id_card/phone/hire_month/
    leave_month/note，外加 staff_type（仅当文件含「类型」列时才有值）。
    """

    name: str
    staff_type: str = ""
    note: str = ""
    code: str = ""
    id_card: str = ""
    phone: str = ""
    hire_month: str = ""
    leave_month: str = ""


def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


# 各列表头的模糊匹配关键字（匹配时忽略空白，由 norm_header 统一）。
# key → 关键字元组，任一关键字命中即视为该列。
_HEADER_HINTS = {
    "name": ("姓名",),
    "staff_type": ("类型",),
    "note": ("备注",),
    "code": ("编号",),
    "id_card": ("身份证",),
    "phone": ("手机",),
    "hire_month": ("入职",),
    "leave_month": ("离职",),
}


def _find_header_row(rows: List[List[str]]) -> int:
    """定位表头行：只需包含「姓名」（表头比较忽略空白）。"""
    want = norm_header("姓名")
    for i, row in enumerate(rows):
        names = [norm_header(_cell_text(c)) for c in row]
        if any(want in n for n in names):
            return i
    return -1


def _column_index(header: List[str], key: str) -> int:
    """返回第一个表头含 key 对应关键字的列号；找不到返回 -1。"""
    hints = _HEADER_HINTS[key]
    for i, h in enumerate(header):
        nh = norm_header(_cell_text(h))
        if any(k in nh for k in hints):
            return i
    return -1


def parse_staff_file(path: str) -> Tuple[List[RosterRow], str]:
    """解析花名册文件，返回 ([RosterRow, ...], file_hash)。

    表头只需含「姓名」；类型列可选（职工清单仍兼容）。编号 / 身份证 / 手机 / 入职 /
    离职 / 备注 各列存在则读取，不存在则留空，绝不报错。

    支持 .xls / .xlsx / .xlsm；找不到含「姓名」的表头 → ImportError_。
    """
    p = Path(path)
    if not p.exists():
        raise ImportError_(f"文件不存在: {path}")
    suffix = p.suffix.lower()
    rows: List[List[str]] = []

    if suffix in (".xlsx", ".xlsm"):
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
        raise ImportError_("未找到表头（需包含'姓名'列）")

    header = rows[hr]
    idx = {k: _column_index(header, k) for k in _HEADER_HINTS}

    staff: List[RosterRow] = []

    def _get(k: str) -> str:
        i = idx[k]
        return _cell_text(row[i]) if i >= 0 and i < len(row) else ""

    for row in rows[hr + 1:]:
        name = _get("name")
        if not name:
            continue
        staff.append(RosterRow(
            name=name,
            staff_type=_get("staff_type"),
            note=_get("note"),
            code=_get("code"),
            id_card=_get("id_card"),
            phone=_get("phone"),
            hire_month=_get("hire_month"),
            leave_month=_get("leave_month"),
        ))

    if not staff:
        raise ImportError_("文件中没有有效的职工数据")

    file_hash = hashlib.md5(p.read_bytes()).hexdigest()
    return staff, file_hash
