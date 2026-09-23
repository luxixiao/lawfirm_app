"""计算表导出 Excel（spec: calc_engine_spec.md §7 / §11.2）

导出规则（定稿）：
- **带公式版**：用户手写的**纯单元格引用公式**（=A1+B1、=SUM(A1:A10)）保留为
  真实 Excel 公式；求值链含 `DATA()`/`PARAM()` 的格子（含混合公式如
  =DATA(...)*0.3）一律填计算值——外部 Excel 不识别 DATA()；
  跨表引用（Sheet2!A1）也填值（单表导出不合并 sheet，保留公式会断链）。
- **不带公式版**：所有格子一律写计算值。
- 数值写数字类型（Excel 可直接再算），错误值写其代码文本（如 #REF!）。
"""
from __future__ import annotations

import re

from app.engine.calc_eval import CalcEvaluator
from app.engine.calc_formula import ErrVal, classify_cell

# DATA( / PARAM( 调用；含混合公式（如 =A1+DATA(...)）一并判为"填值"
_DATA_PARAM_RE = re.compile(r"\bDATA\s*\(|\bPARAM\s*\(", re.IGNORECASE)


def should_keep_formula(raw: str) -> bool:
    """该公式能否保留为真实 Excel 公式（不含 DATA/PARAM、不含跨表引用）。"""
    if not raw or not raw.startswith("="):
        return False
    if _DATA_PARAM_RE.search(raw):
        return False
    if "!" in raw:            # 本引擎中 '!' 仅用于跨表前缀
        return False
    return True


def export_sheet(sheet_id: int, path: str, with_formula: bool = True,
                 conn=None) -> str:
    """导出一张计算表为 xlsx，返回写入路径。"""
    from openpyxl import Workbook   # 延迟导入：无 openpyxl 时纯逻辑仍可用

    ev = CalcEvaluator(conn, sheet_id)
    try:
        sheet_name = ev.cur_sheet()
        content = ev.sheets.get(sheet_name) or {}
        cells = content.get("cells") or {}

        wb = Workbook()
        ws = wb.active
        ws.title = _safe_title(sheet_name)

        for key, cell in cells.items():
            try:
                r, c = (int(x) for x in key.split(","))
            except (ValueError, AttributeError):
                continue
            raw = cell.get("raw")
            kind = cell.get("kind") or classify_cell(raw)
            cellref = ws.cell(row=r + 1, column=c + 1)

            if kind == "formula" and with_formula and should_keep_formula(str(raw)):
                cellref.value = str(raw)
                continue
            v = ev.cell_value(sheet_name, r, c)
            if isinstance(v, ErrVal):
                cellref.value = v.code
            elif isinstance(v, str) and v == "":
                continue
            else:
                cellref.value = v
    finally:
        # P1-2：求值/写格中途抛异常（如公式错误）也必须关闭连接
        ev.close()

    wb.save(path)
    return path


def _safe_title(name: str) -> str:
    r"""Excel 工作表标题：禁 []:*?/ 与反斜杠，且 ≤31 字符。"""
    clean = re.sub(r"[\[\]:*?/\\]", "_", str(name or "Sheet1")).strip() or "Sheet1"
    return clean[:31]


def suggest_filename(sheet_name: str, with_formula: bool = True) -> str:
    suffix = "" if with_formula else "（仅值）"
    return f"{_safe_title(sheet_name)}{suffix}.xlsx"
