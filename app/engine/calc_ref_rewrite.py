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
