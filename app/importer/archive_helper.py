"""发票台账源文件存档读取助手（行级溯源）

- resolve_archive: 把 import_batch.archive_path（相对项目根）解析为绝对路径
- read_archive_row: 读存档文件指定 sheet 的第 row_no 行（1 基，含表头行前的行）
- 行号约定与 ledger_import 一致：row_no = enumerate(rows[hr+1:], start=hr+2)

仅用于「导入校验中心」与「右键查看台账信息」溯源展示，不参与写库。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from app.importer.excel_reader import find_header_row, read_sheet

# app/importer/archive_helper.py -> parent.parent.parent = lawfirm_app（项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# _PARENT = 项目根的父目录（工作区根：importer 存档时 archive_path 相对此根）
_PARENT = PROJECT_ROOT.parent


def resolve_archive(archive_path: str) -> Path:
    """把存档相对路径解析为绝对路径（已是绝对则原样返回）。

    兼容两种相对基准：
    - importer 存档时存的是相对工作区根：lawfirm_app/data/archive/...
    - 历史版本可能存相对项目根：data/archive/...
    """
    p = Path(archive_path)
    if p.is_absolute():
        return p
    if p.parts and p.parts[0] == PROJECT_ROOT.name:
        cand = _PARENT / p
        if cand.exists():
            return cand
    return PROJECT_ROOT / p


def read_archive_row(
    archive_path: str, sheet_name: str, row_no: int
) -> Optional[Tuple[List[str], List[str]]]:
    """读取存档文件中指定 sheet 的第 row_no 行（1 基）。

    返回 (header, row)（均为文本列表），文件/行列不存在返回 None。
    header 为匹配到的表头行；row 为目标数据行。
    """
    path = resolve_archive(archive_path)
    if not path.exists() or row_no <= 0:
        return None
    try:
        rows = read_sheet(str(path), sheet_name=sheet_name)
    except Exception:  # noqa: BLE001 读不到（如 sheet 名不匹配）直接判无溯源
        return None
    if not rows or row_no > len(rows):
        return None
    hr = find_header_row(rows, ["发票号码", "经办人", "金额"])
    header = rows[hr] if hr >= 0 else (rows[0] if rows else [])
    target = rows[row_no - 1]
    return (
        [str(c) if c is not None else "" for c in header],
        [str(c) if c is not None else "" for c in target],
    )
