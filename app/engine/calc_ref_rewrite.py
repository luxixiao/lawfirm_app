"""公式引用重写（阶段0 底座，供 G2/G4 复用）。

把"插入/删除行列"和"复制/填充"两类引用平移，统一为：
    parse(raw) → _walk(ast, T) → render(ast)
其中 T 是对单个 CellRef 坐标 (row0,col0) 的纯变换。

设计要点（见 docs/calc_sheet_ux_implementation_plan.md §2.4）：
- 跨表引用（sheet is not None，如 `Sheet2!A1`）**不平移**，原样保留。
- 绝对引用（abs_row / abs_col）**不平移**。
- 插入：非绝对且坐标 >= at → +delta；区域两端各自独立套 T，自然扩张/平移。
- 删除：非绝对且落在 [at, at+delta) → 变 #REF!；>= at+delta → -delta；区域任一端命中删除区间 → 整片变 #REF!。
- 复制/填充：非绝对 → +dr/+dc，无扩张语义。
- 解析失败 → 原样返回（防御式，不丢公式）。
"""
from __future__ import annotations

from typing import Set

from app.engine.calc_formula import (
    BinOp, CellRef, ErrRef, FuncCall, Parser, ParseError, RangeRef, UnaryOp,
    _SheetParam,
)
from app.engine.calc_ast_render import render


def shift_refs(formula: str, axis: str, at: int, delta: int, *, insert: bool) -> str:
    """插入/删除行列时改写公式内引用。

    axis: 'row' | 'col'；at: 0 基插入/删除位置；delta: 受影响行/列数(>0)；
    insert=True 插入，False 删除。仅对 sheet is None 的自引用生效（跨表不平移）。
    """
    return _rewrite(formula, _make_shift_T(axis, at, delta, insert))


def translate_refs(formula: str, dr: int, dc: int) -> str:
    """复制/填充时按位移平移相对引用；绝对引用不动。无扩张语义。"""
    return _rewrite(formula, _make_translate_T(dr, dc))


def rename_sheet_refs(formula: str, old: str, new: str) -> str:
    """把公式中指向 old 表的**跨表引用**整体改成 new 表（阶段5 G10）。

    为什么**不能**纯文本替换 `Old!`→`New!`（阶段5 评估 B1）：
      - 误伤字符串字面量：文本格 `="Old!A1"` 会被改成 `="New!A1"`；
      - 前缀误判：old="Old" 时 `=Old2!A1` 会被改成 `=New2!A1`。
    因此走 parse → 只改 CellRef / RangeRef / _SheetParam 的 sheet 属性 → render。
    本地引用（sheet is None）、他表引用、绝对引用**均不动**；解析失败原样返回。

    匹配为**精确匹配**（大小写敏感）——与 CalcEvaluator.has_sheet 的精确查表保持一致。
    """
    has_eq = formula.startswith("=")
    body = formula[1:] if has_eq else formula
    try:
        ast = Parser(body).parse()
    except ParseError:
        return formula
    return ("=" if has_eq else "") + render(_walk_rename(ast, old, new))


def formula_sheet_refs(formula: str) -> Set[str]:
    """收集公式里引用到的**表名**集合（导出时算"要一起导出的表"闭包用）。

    只收跨表引用（sheet 非 None）；跨表区域/带引号表名同样覆盖。
    解析失败返回**空集**（保守：不因此把公式误判为非法，只是闭包可能不完整）。
    """
    body = formula[1:] if formula.startswith("=") else formula
    try:
        ast = Parser(body).parse()
    except ParseError:
        return set()
    out: Set[str] = set()
    _collect_sheets(ast, out)
    return out


def _collect_sheets(node, out: Set[str]) -> None:
    """递归收集节点里的表名。注意**不能**复用 _walk：它对跨表节点原样返回且不聚合。"""
    if isinstance(node, CellRef):
        if node.sheet:
            out.add(node.sheet)
        return
    if isinstance(node, RangeRef):
        if node.sheet:
            out.add(node.sheet)
        return
    if isinstance(node, BinOp):
        _collect_sheets(node.l, out)
        _collect_sheets(node.r, out)
        return
    if isinstance(node, UnaryOp):
        _collect_sheets(node.operand, out)
        return
    if isinstance(node, FuncCall):
        for a in node.args:
            _collect_sheets(a, out)
        return
    if isinstance(node, _SheetParam):
        if node.sheet:
            out.add(node.sheet)
        for a in node.args:
            _collect_sheets(a, out)


def _walk_rename(node, old: str, new: str):
    """递归改写指向 old 的跨表引用。

    **不能复用 `_walk`**：它在 `node.sheet is not None` 时直接原样返回（跨表不平移），
    而这里恰恰**只**改跨表节点，语义相反，故单独一支。
    """
    if isinstance(node, CellRef):
        if node.sheet == old:
            return CellRef(new, node.row0, node.col0, node.abs_row, node.abs_col)
        return node
    if isinstance(node, RangeRef):
        if node.sheet == old:
            return RangeRef(new, node.r1, node.c1, node.r2, node.c2,
                            node.abs_r1, node.abs_c1, node.abs_r2, node.abs_c2)
        return node
    if isinstance(node, BinOp):
        return BinOp(node.op, _walk_rename(node.l, old, new),
                     _walk_rename(node.r, old, new))
    if isinstance(node, UnaryOp):
        return UnaryOp(node.op, _walk_rename(node.operand, old, new))
    if isinstance(node, FuncCall):
        return FuncCall(node.name, [_walk_rename(a, old, new) for a in node.args])
    if isinstance(node, _SheetParam):
        # Sheet!PARAM("x")：表前缀同样要跟着改名，否则该表参数取不到
        return _SheetParam(new if node.sheet == old else node.sheet,
                           [_walk_rename(a, old, new) for a in node.args])
    return node


# ---------------------------------------------------------------------------
# 内部：通用改写骨架
# ---------------------------------------------------------------------------

def _rewrite(formula: str, T) -> str:
    """解析 → _walk(T) → render；保留原始 '=' 前缀；解析失败原样返回。"""
    has_eq = formula.startswith("=")
    body = formula[1:] if has_eq else formula
    try:
        ast = Parser(body).parse()
    except ParseError:
        return formula
    new = _walk(ast, T)
    return ("=" if has_eq else "") + render(new)


def _walk(node, T):
    """递归遍历 AST，对 CellRef/RangeRef 套用变换 T；跨表引用原样保留。"""
    if isinstance(node, CellRef):
        if node.sheet is not None:
            return node  # 跨表引用不平移
        return T(node)
    if isinstance(node, RangeRef):
        if node.sheet is not None:
            return node  # 跨表区域不平移
        a = _apply_corner(node.r1, node.c1, node.abs_r1, node.abs_c1, T)
        b = _apply_corner(node.r2, node.c2, node.abs_r2, node.abs_c2, T)
        # 任一端命中删除区间 → 整片变 #REF!（与 Excel 一致）
        if isinstance(a, ErrRef) or isinstance(b, ErrRef):
            return ErrRef()
        return RangeRef(None, a[0], a[1], b[0], b[1],
                        a[2], a[3], b[2], b[3])
    if isinstance(node, BinOp):
        return BinOp(node.op, _walk(node.l, T), _walk(node.r, T))
    if isinstance(node, UnaryOp):
        return UnaryOp(node.op, _walk(node.operand, T))
    if isinstance(node, FuncCall):
        return FuncCall(node.name, [_walk(a, T) for a in node.args])
    if isinstance(node, _SheetParam):
        # Sheet2!PARAM(...) 带表前缀，无内部单元格引用，原样保留
        return node
    return node


def _apply_corner(r, c, ar, ac, T):
    """把单角当作 CellRef 套 T，返回 (row0, col0, abs_row, abs_col)；命中删除→ErrRef。"""
    tmp = CellRef(None, r, c, ar, ac)
    res = T(tmp)
    if isinstance(res, ErrRef):
        return ErrRef()
    return (res.row0, res.col0, res.abs_row, res.abs_col)


# ---------------------------------------------------------------------------
# 平移变换 T（对单个 CellRef 作用）
# ---------------------------------------------------------------------------

def _make_shift_T(axis: str, at: int, delta: int, insert: bool):
    """对单个 CellRef 作用：返回**新** CellRef / ErrRef，绝不原地改写入参（G0-5 纯函数化）。

    row：非绝对且 >= at（插入）/ >= at+delta（删除）→ ±delta；删除落在 [at, at+delta) → #REF!。
    col 同理。绝对引用不平移。
    """
    def T(ref: CellRef):
        if axis == "row":
            if ref.abs_row:
                return CellRef(ref.sheet, ref.row0, ref.col0, ref.abs_row, ref.abs_col)
            if insert:
                new_row = ref.row0 + delta if ref.row0 >= at else ref.row0
            else:
                if at <= ref.row0 < at + delta:
                    return ErrRef()
                new_row = ref.row0 - delta if ref.row0 >= at + delta else ref.row0
            return CellRef(ref.sheet, new_row, ref.col0, ref.abs_row, ref.abs_col)
        else:  # col
            if ref.abs_col:
                return CellRef(ref.sheet, ref.row0, ref.col0, ref.abs_row, ref.abs_col)
            if insert:
                new_col = ref.col0 + delta if ref.col0 >= at else ref.col0
            else:
                if at <= ref.col0 < at + delta:
                    return ErrRef()
                new_col = ref.col0 - delta if ref.col0 >= at + delta else ref.col0
            return CellRef(ref.sheet, ref.row0, new_col, ref.abs_row, ref.abs_col)
    return T


def _make_translate_T(dr: int, dc: int):
    def T(ref: CellRef) -> CellRef:
        new_row0 = ref.row0 if ref.abs_row else ref.row0 + dr
        new_col0 = ref.col0 if ref.abs_col else ref.col0 + dc
        # 平移后坐标越界（<0）→ 引用失效（Excel：复制/填充到越界位置产生 #REF!）。
        # G0-1 修复：此前负位移会生成非法引用（如 =A1 左移→=1、上移→=A0）并静默错值。
        if new_row0 < 0 or new_col0 < 0:
            return ErrRef()
        return CellRef(None, new_row0, new_col0, ref.abs_row, ref.abs_col)
    return T
