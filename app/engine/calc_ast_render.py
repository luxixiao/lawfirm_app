"""calc_formula AST → 文本反向序列化器（阶段0 底座）。

与 Parser 严格互逆（canonical 规范形）：render(parse(x)) 应稳定，供
阶段3（插入/删除行列引用重写）与阶段4（复制/填充平移）复用。

设计要点：
- 数值按 int 值→无小数规范化（1.0→"1"）；字符串重新加引号（" 转 ""，但含引号字符串本期不支持解析，见风险 R1）。
- 单元格/区域按 abs_row/abs_col 渲染 $ 锚点：$A$1 / A$1 / $A1 / A1。
- BinOp 最小括号规则依优先级（比较<+-<*/<^<一元），与 Parser 互逆；
  一元负号作子节点或作为二元运算左/右子节点时加括号，保证幂等可重入。
"""
from __future__ import annotations

from app.engine.calc_formula import (
    BinOp, CellRef, ErrRef, FuncCall, Num, RangeRef, Str, UnaryOp,
    _SheetParam, col_to_letters,
)

# 运算符优先级（低 → 高）。用于决定何时加最小括号。
_PREC = {
    "=": 0, "<>": 0, "<": 0, ">": 0, "<=": 0, ">=": 0,
    "+": 1, "-": 1,
    "*": 2, "/": 2,
    "^": 3,
}
# 右结合运算符（仅 ^）；其余左结合。
_ASSOC_RIGHT = {"^"}


def _level(n) -> int:
    """节点在括号决策中的优先级层级；原子/一元负号视为最高。"""
    if isinstance(n, BinOp):
        return _PREC.get(n.op, 1)
    return 99


def render(node) -> str:
    """AST 节点 → 公式正文（不含前导 '='）。"""
    if isinstance(node, Num):
        v = node.v
        if float(v).is_integer():
            return str(int(v))
        return repr(float(v))
    if isinstance(node, Str):
        return '"' + node.v.replace('"', '""') + '"'
    if isinstance(node, CellRef):
        return _render_cell(node.sheet, node.row0, node.col0,
                            node.abs_row, node.abs_col)
    if isinstance(node, RangeRef):
        # 跨表区域前缀只在首端出现一次：Sheet2!A1:B2（非 Sheet2!A1:Sheet2!B2）
        a = _render_cell(None, node.r1, node.c1, node.abs_r1, node.abs_c1)
        b = _render_cell(None, node.r2, node.c2, node.abs_r2, node.abs_c2)
        return (f"{node.sheet}!" if node.sheet else "") + f"{a}:{b}"
    if isinstance(node, ErrRef):
        return "#REF!"
    if isinstance(node, UnaryOp):
        inner = render(node.operand)
        if isinstance(node.operand, (BinOp, UnaryOp)):
            inner = f"({inner})"
        return ("-" if node.op == "-" else "+") + inner
    if isinstance(node, BinOp):
        return _render_binop(node)
    if isinstance(node, FuncCall):
        return f"{node.name}(" + ",".join(render(a) for a in node.args) + ")"
    if isinstance(node, _SheetParam):
        return f"{node.sheet}!PARAM(" + ",".join(render(a) for a in node.args) + ")"
    raise ValueError(f"未知节点：{type(node).__name__}")


def _render_cell(sheet, row0, col0, abs_row=False, abs_col=False) -> str:
    col = ("$" if abs_col else "") + col_to_letters(col0 + 1)
    row = ("$" if abs_row else "") + str(row0 + 1)
    return (f"{sheet}!" if sheet else "") + col + row


def _render_binop(node: BinOp) -> str:
    op = node.op
    plv = _PREC.get(op, 1)
    left = render(node.l)
    right = render(node.r)
    # 左子节点：层级低于父 → 加括号；或是一元负号（父括号内语义需保留）→ 加括号
    if isinstance(node.l, UnaryOp) or _level(node.l) < plv:
        left = f"({left})"
    # 右子节点：层级低于父 → 加括号；同层级且为左结合 → 加括号；一元负号 → 加括号
    if (isinstance(node.r, UnaryOp)
            or _level(node.r) < plv
            or (_level(node.r) == plv and op not in _ASSOC_RIGHT)):
        right = f"({right})"
    return f"{left}{op}{right}"
