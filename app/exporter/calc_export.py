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
from app.engine.calc_formula import (
    BinOp, CellRef, ErrRef, ErrVal, FuncCall, Parser, ParseError, RangeRef,
    UnaryOp, classify_cell, _SheetParam,
)
from app.engine.calc_ref_rewrite import formula_sheet_refs, rename_sheet_refs
from app.engine.calc_sheet import EXCEL_TITLE_MAX

# Excel 里不存在这两个函数，求值链含它们（含混合公式）一律填计算值
_HOST_ONLY_FUNCS = {"DATA", "PARAM"}


def analyze_formula(raw: str):
    """返回 (has_host_func, has_err_ref, sheets)：导出时决定能否保留为 Excel 公式。

    - has_host_func：含 DATA()/PARAM() → Excel 无此函数，必须填值
    - has_err_ref：含 `#REF!` 伪节点 → **Excel 不接受 `#REF!` 字面量语法**，
      写成公式会导致 Excel 打开即报错（G2 删行列会产生这类公式），必须填值
    - sheets：引用的表名集合（用于判断被引用表是否也在导出集合内）
    - 解析失败 → (True, True, set())：保守处理，一律填值
    """
    body = raw[1:] if raw.startswith("=") else raw
    try:
        ast = Parser(body).parse()
    except ParseError:
        return True, True, set()
    has_host = False
    has_err = False
    sheets = set()
    stack = [ast]
    while stack:
        n = stack.pop()
        if isinstance(n, ErrRef):
            has_err = True
        elif isinstance(n, FuncCall):
            if n.name.upper() in _HOST_ONLY_FUNCS:
                has_host = True
            stack.extend(n.args)
        elif isinstance(n, _SheetParam):
            if n.sheet:
                sheets.add(n.sheet)
            stack.extend(n.args)
        elif isinstance(n, CellRef):
            if n.sheet:
                sheets.add(n.sheet)
        elif isinstance(n, RangeRef):
            if n.sheet:
                sheets.add(n.sheet)
        elif isinstance(n, BinOp):
            stack.extend((n.l, n.r))
        elif isinstance(n, UnaryOp):
            stack.append(n.operand)
    return has_host, has_err, sheets


def should_keep_formula(raw: str, known_sheets=None) -> bool:
    """该公式能否保留为真实 Excel 公式。

    判定走 **AST**（不再用正则扫 `!`）：
    - 含 DATA()/PARAM() → 否；含 `#REF!` → 否；
    - 引用的表不在导出集合 `known_sheets` 内 → 否（否则 Excel 里就是 #REF!）；
    - 其余（**含跨表引用**）→ 是，前提是那些表已一起导出（见 export_sheet）。
    """
    if not raw or not raw.startswith("="):
        return False
    has_host, has_err, sheets = analyze_formula(raw)
    if has_host or has_err:
        return False
    if known_sheets is not None and any(s not in known_sheets for s in sheets):
        return False
    return True


def export_sheet(sheet_id: int, path: str, with_formula: bool = True,
                 conn=None) -> str:
    """导出一张计算表为 xlsx，返回写入路径。

    **多表导出**：除目标表外，还会把它**递归引用到的所有表**一起写进同一个 workbook。
    原因：跨表公式（如 `='2026-01'!A1`）只有在被引用的 worksheet 也存在于工作簿里时
    Excel 才能解析；否则打开就是 `#REF!`。这也是以前"见 `!` 就填值"的根因。
    """
    from openpyxl import Workbook   # 延迟导入：无 openpyxl 时纯逻辑仍可用

    ev = CalcEvaluator(conn, sheet_id)
    try:
        cur = ev.cur_sheet()
        all_sheets = ev.sheets or {}
        names = _referenced_closure(all_sheets, cur)
        # 目标表放第一个（Excel 打开时默认显示它）
        ordered = [cur] + sorted(n for n in names if n != cur)

        wb = Workbook()
        wb.active.title = _safe_title(cur)
        for n in ordered[1:]:
            wb.create_sheet(_safe_title(n))

        title_of = {n: _safe_title(n) for n in ordered}
        # 标题漂移兜底：老数据可能有超长名/Excel 禁用字符，此时把公式里的表名改写成实际标题
        drift = {n: t for n, t in title_of.items() if t != n}

        for n in ordered:
            _fill_sheet(wb[title_of[n]], ev, n, with_formula, set(names), drift)

        wb.save(path)
        return path
    finally:
        # P1-2：求值/写格中途抛异常（如公式错误）也必须关闭连接
        ev.close()


def _referenced_closure(all_sheets: dict, start: str) -> set:
    """目标表 + 它递归引用到的所有表（只在库内真实存在的表名里传播）。"""
    out, stack = set(), [start]
    while stack:
        n = stack.pop()
        if n in out or n not in all_sheets:
            continue
        out.add(n)
        cells = (all_sheets.get(n) or {}).get("cells") or {}
        for cell in cells.values():
            if not isinstance(cell, dict):
                continue
            raw = cell.get("raw")
            if isinstance(raw, str) and raw.startswith("="):
                for s in formula_sheet_refs(raw):
                    if s not in out:
                        stack.append(s)
    return out


def _fill_sheet(ws, ev, name: str, with_formula: bool,
                known_sheets: set, drift: dict) -> None:
    """把一张表的内容写进 worksheet：能保留公式的写公式，其余写计算值。"""
    content = ev.sheets.get(name) or {}
    for key, cell in (content.get("cells") or {}).items():
        try:
            r, c = (int(x) for x in key.split(","))
        except (ValueError, AttributeError):
            continue
        raw = cell.get("raw")
        kind = cell.get("kind") or classify_cell(raw)
        raw_s = raw if isinstance(raw, str) else ""
        cellref = ws.cell(row=r + 1, column=c + 1)

        if kind == "formula" and with_formula and should_keep_formula(raw_s, known_sheets):
            cellref.value = _apply_drift(raw_s, drift)
            continue
        v = ev.cell_value(name, r, c)
        if isinstance(v, ErrVal):
            cellref.value = v.code
        elif isinstance(v, str) and v == "":
            continue
        else:
            cellref.value = v


def _apply_drift(raw: str, drift: dict) -> str:
    """标题漂移时把公式里的表名改写成实际 worksheet 标题（正常数据下 drift 为空）。"""
    for n, t in drift.items():
        raw = rename_sheet_refs(raw, n, t)
    return raw


def _safe_title(name: str) -> str:
    r"""Excel 工作表标题：禁 []:*?/ 与反斜杠，且 ≤31 字符。

    正常情况下 `validate_sheet_name` 已在入库前拦掉这两类名字，因此 **title == 表名**；
    本函数只是老数据的兜底（真触发时由 `_apply_drift` 同步改写公式里的表名）。
    """
    clean = re.sub(r"[\[\]:*?/\\]", "_", str(name or "Sheet1")).strip() or "Sheet1"
    return clean[:EXCEL_TITLE_MAX]


def suggest_filename(sheet_name: str, with_formula: bool = True) -> str:
    suffix = "" if with_formula else "（仅值）"
    return f"{_safe_title(sheet_name)}{suffix}.xlsx"
