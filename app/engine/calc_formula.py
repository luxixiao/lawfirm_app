"""分成计算引擎 — 公式求值内核（spec: calc_engine_spec.md §3/§4）

纯 Python 自建：tokenizer → 递归下降 parser（AST）→ evaluator。
- 运算符：+ - * / ^ () 一元负号；比较 = <> < > <= >=（供 IF 用）
- 函数：SUM / ROUND / AVERAGE / IF / MIN / MAX / ABS / DATA / PARAM
- 引用：A1、A1:B10（区域）；跨表 Sheet2!A1、Sheet2!A1:B10；Sheet2!PARAM("x")
- Excel 语义：空格=0；SUM/AVERAGE/MIN/MAX 忽略文本与空格；错误值传播；
  IF 惰性求值（只算命中的分支）；ROUND 为四舍五入（half-up，非银行家舍入）
- 错误值：#REF!（表/引用不存在、DATA取数失败）、#NAME?（未知函数/参数）、
  #CIRC!（循环引用）、#VALUE!（类型错）、#DIV/0!、#ERROR!（语法错）
- 请求级缓存：同一 Engine 实例内每格只求值一次；栈式环检测。

宿主程序通过实现 CellProvider 接入网格（阶段3 calc_sheet JSON 整表）与
数据层（阶段1 calc_data.CalcData + calc_indicator 自定义指标）。
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# 值模型
# ---------------------------------------------------------------------------

class ErrVal:
    """Excel 风格错误值。"""

    def __init__(self, code: str, msg: str = ""):
        self.code = code
        self.msg = msg

    def __repr__(self):
        return self.code

    def __eq__(self, other):
        return isinstance(other, ErrVal) and other.code == self.code


ERR_REF = "#REF!"
ERR_NAME = "#NAME?"
ERR_CIRC = "#CIRC!"
ERR_VALUE = "#VALUE!"
ERR_DIV = "#DIV/0!"
ERR_NUM = "#NUM!"
ERR_SYNTAX = "#ERROR!"
# 取数层**非预期**异常（表结构缺失 / 驱动错误 / 代码 bug）专用码。
# 为什么要单独一个码：以前这类崩溃被一律兜成 #REF!，与「真·引用失效」长得一模一样，
# 于是「少了一个迁移列」这种致命问题在网格里看起来只是普通的引用断开，
# 排查方向直接跑偏（2026-09-25 在 tests/_smoke_calc_export.py 上实测踩到）。
ERR_UNEXPECTED = "#SYSERR!"


class CalcDataFailure(Exception):
    """取数层异常**标记基类**（`calc_data.CalcDataError` 继承它）。

    放在本叶子模块是为了让 `calc_data` 单向依赖它而不产生循环导入
    （`calc_formula` 不 import 任何 app.*，是依赖图的叶子）。
    引擎据此区分：
      - `CalcDataFailure` 子类且 `unexpected=False` → 业务错误，呈现 `#REF!`；
      - `unexpected=True` 或**非**该类异常 → `#SYSERR!` + 写日志告警。
    """


def _log_unexpected(msg: str) -> None:
    """非预期取数异常写日志：data/app_debug.log（%LOCALAPPDATA%/lawfirm_app/logs）+ stderr。
    **不受 LAWFIRM_DIAG 门控**——真出错就要留痕，不能因为没开诊断而静默。
    刻意延迟 import app.diag：它依赖 PySide6，而在**出错时**才导入，
    保证纯逻辑（无 Qt）环境下 import calc_formula 依然可用。
    """
    try:
        from app.diag import get_logger, setup_logging
        setup_logging()
        get_logger().warning(msg)
    except Exception:      # 连日志都失败也不能把异常二次抛出
        logging.getLogger(__name__).warning(msg)


def _wrap_unexpected(exc: BaseException, person, indicator, year, month) -> ErrVal:
    """把非预期取数异常转成 `#SYSERR!` 并落一条带完整上下文的告警日志。"""
    period = f"{year}年{month}月" if month else f"{year}年全年"
    root = getattr(exc, "__cause__", None) or exc
    detail = (f"DATA 取数异常：职工={person!r} 指标={indicator!r} 账期={period} | "
              f"{type(root).__name__}: {root}")
    _log_unexpected(detail)
    return ErrVal(ERR_UNEXPECTED, detail)


def is_err(v) -> bool:
    return isinstance(v, ErrVal)


# ---------------------------------------------------------------------------
# A1 坐标换算（列 1 基：A=1；行 1 基）
# ---------------------------------------------------------------------------

_CELL_RE = re.compile(r"^([A-Za-z]{1,3})([0-9]{1,5})$")


def letters_to_col(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def col_to_letters(col: int) -> str:
    s = ""
    while col > 0:
        col, r = divmod(col - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def a1_to_rc(ref: str) -> Optional[Tuple[int, int]]:
    """'A1' → (row0, col0)（0 基）；不匹配返回 None。"""
    m = _CELL_RE.match(ref.strip())
    if not m:
        return None
    return int(m.group(2)) - 1, letters_to_col(m.group(1)) - 1


def rc_to_a1(row0: int, col0: int) -> str:
    return f"{col_to_letters(col0 + 1)}{row0 + 1}"


def classify_cell(raw) -> str:
    """单元格 kind 分类：formula / number / text / blank。"""
    if raw is None or raw == "":
        return "blank"
    if isinstance(raw, str) and raw.startswith("="):
        return "formula"
    if isinstance(raw, (int, float)):
        return "number"
    try:
        float(str(raw).strip())
        return "number"
    except (ValueError, TypeError):
        return "text"


# ---------------------------------------------------------------------------
# A1 引用解析（含绝对引用 $）
# ---------------------------------------------------------------------------

# G0-4 完善：与 spec §2.2 ref 分支对齐——用命名组显式捕获列/行绝对符，
# 不再用 tok[len(col_letters):] 下标推断（脆弱且偏离 spec），功能等价。
_A1_RE = re.compile(r"^(?P<ac>\$?)(?P<col>[A-Za-z]{1,3})(?P<ar>\$?)(?P<row>[0-9]{1,5})$")


def _parse_ref_part(tok: str):
    """'$A$1' / '$A1' / 'A$1' / 'A1' → (row0, col0, abs_row, abs_col)；非单元格引用→None。

    row0/col0 为 0 基；abs_row/abs_col 标记该轴是否为绝对引用（不随插行/复制平移）。
    ac = 列绝对符（$ 在列字母前），ar = 行绝对符（$ 在列字母与数字之间）。
    """
    m = _A1_RE.match(tok)
    if not m:
        return None
    col0 = letters_to_col(m.group("col")) - 1
    row0 = int(m.group("row")) - 1
    abs_col = bool(m.group("ac"))
    abs_row = bool(m.group("ar"))
    return (row0, col0, abs_row, abs_col)


# ---------------------------------------------------------------------------
# tokenizer
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<errref>\#REF!)
      | (?P<num>\d+(?:\.\d+)?)
      | (?P<str>"[^"]*")
      | (?P<qname>'(?:[^']|'')*')
      | (?P<ident>[A-Za-z_\u4e00-\u9fff$][A-Za-z0-9_\u4e00-\u9fff$]*)
      | (?P<op><>|<=|>=|[-+*/^()<>=!,:])
    )""",
    re.X,
)


def tokenize(src: str) -> List[Tuple[str, str]]:
    """返回 [(kind, text)]，kind ∈ num/str/ident/op。无法识别→带 ERR 标记尾项。"""
    tokens: List[Tuple[str, str]] = []
    pos, n = 0, len(src)
    while pos < n:
        m = _TOKEN_RE.match(src, pos)
        if not m or m.end() == pos:
            rest = src[pos:].strip()
            if rest:
                tokens.append(("err", rest))
            break
        kind = m.lastgroup
        tokens.append((kind, m.group(kind)))
        pos = m.end()
    return tokens


# ---------------------------------------------------------------------------
# 表名引用规则（Excel 单引号表名：'2026-01'!A1）
# ---------------------------------------------------------------------------

# 「简单名」= 无需单引号即可写进 `表名!A1` 的名字。
# **必须与上面 tokenizer 的 ident 规则严格互逆**，否则 render 出来的公式自己都解析不了
# （G2 插删行列 / G4 复制填充 / G10 改名 都是 parse→变换→render，引号错了会静默退化）。
_SIMPLE_SHEET_RE = re.compile(r"^[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*$")
# Excel 保留字面量：作表名时必须引号，否则会被当成布尔值
_EXCEL_RESERVED_NAMES = {"TRUE", "FALSE"}


def sheet_needs_quotes(name: str) -> bool:
    """Excel 规则：表名出现在引用里（`表名!A1`）时是否需要单引号包裹。

    需要引号：空名、非"简单名"（含空格/-/. 等、或以数字开头）、
    形如单元格地址（A1/AB12）、是 TRUE/FALSE 字面量。
    """
    if not name:
        return False
    if not _SIMPLE_SHEET_RE.match(name):
        return True
    if name.upper() in _EXCEL_RESERVED_NAMES:
        return True
    return bool(_CELL_RE.match(name))


def quote_sheet_name(name: str) -> str:
    """渲染引用里的表前缀（**含结尾 `!`**），按 Excel 规则决定是否加单引号。

    名字里的 `'` 按 Excel 约定转义为 `''`。
    """
    if not name:
        return ""
    if sheet_needs_quotes(name):
        return "'" + name.replace("'", "''") + "'!"
    return f"{name}!"


def unquote_sheet_name(text: str) -> str:
    """把词法层拿到的 `'xxx'` 还原成真实表名（剥外层引号 + `''` 转义还原）。"""
    if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
        text = text[1:-1]
    return text.replace("''", "'")


# ---------------------------------------------------------------------------
# AST 节点
# ---------------------------------------------------------------------------

class Num:
    def __init__(self, v: float):
        self.v = v


class Str:
    def __init__(self, v: str):
        self.v = v


class ErrRef:
    """被删除行列命中的引用失效伪节点（渲染为 #REF!）。"""


class CellRef:
    def __init__(self, sheet: Optional[str], row0: int, col0: int,
                 abs_row: bool = False, abs_col: bool = False):
        self.sheet, self.row0, self.col0 = sheet, row0, col0
        self.abs_row, self.abs_col = abs_row, abs_col


class RangeRef:
    def __init__(self, sheet: Optional[str], r1: int, c1: int, r2: int, c2: int,
                 abs_r1: bool = False, abs_c1: bool = False,
                 abs_r2: bool = False, abs_c2: bool = False):
        self.sheet = sheet
        self.r1, self.c1 = min(r1, r2), min(c1, c2)
        self.r2, self.c2 = max(r1, r2), max(c1, c2)
        self.abs_r1, self.abs_c1 = abs_r1, abs_c1
        self.abs_r2, self.abs_c2 = abs_r2, abs_c2


class BinOp:
    def __init__(self, op: str, l, r):
        self.op, self.l, self.r = op, l, r


class UnaryOp:
    def __init__(self, op: str, operand):
        self.op, self.operand = op, operand


class FuncCall:
    def __init__(self, name: str, args: list):
        self.name, self.args = name.upper(), args


class ParseError(Exception):
    pass


# ---------------------------------------------------------------------------
# parser（递归下降）
# ---------------------------------------------------------------------------

class Parser:
    def __init__(self, src: str):
        self.src = src
        self.toks = tokenize(src)
        self.i = 0

    # --- 游标 ---
    def _peek(self) -> Optional[Tuple[str, str]]:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _next(self) -> Tuple[str, str]:
        t = self._peek()
        if t is None:
            raise ParseError("表达式意外结束")
        self.i += 1
        return t

    def _expect_op(self, op: str) -> None:
        k, v = self._next()
        if k != "op" or v != op:
            raise ParseError(f"期望 '{op}'，实际 '{v}'")

    # --- 文法 ---
    def parse(self):
        node = self._comparison()
        if self._peek() is not None:
            raise ParseError(f"多余内容：'{self._peek()[1]}'")
        return node

    def _comparison(self):
        node = self._additive()
        while True:
            t = self._peek()
            if t and t[0] == "op" and t[1] in ("=", "<>", "<", ">", "<=", ">="):
                self._next()
                node = BinOp(t[1], node, self._additive())
            else:
                return node

    def _additive(self):
        node = self._term()
        while True:
            t = self._peek()
            if t and t[0] == "op" and t[1] in ("+", "-"):
                self._next()
                node = BinOp(t[1], node, self._term())
            else:
                return node

    def _term(self):
        node = self._power()
        while True:
            t = self._peek()
            if t and t[0] == "op" and t[1] in ("*", "/"):
                self._next()
                node = BinOp(t[1], node, self._power())
            else:
                return node

    def _power(self):
        node = self._unary()
        while True:
            t = self._peek()
            if t and t[0] == "op" and t[1] == "^":
                self._next()
                node = BinOp("^", node, self._unary())
            else:
                return node

    def _unary(self):
        t = self._peek()
        if t and t[0] == "op" and t[1] in ("+", "-"):
            self._next()
            return UnaryOp(t[1], self._unary())
        return self._primary()

    def _primary(self):
        k, v = self._next()
        if k == "num":
            return Num(float(v))
        if k == "str":
            return Str(v[1:-1])
        if k == "op" and v == "(":
            node = self._comparison()
            self._expect_op(")")
            return node
        if k == "errref":
            return ErrRef()
        # 单引号表名前缀：'2026-01'!A1（名字里的 ' 写成 ''）
        if k == "qname":
            nxt = self._peek()
            if nxt and nxt[0] == "op" and nxt[1] == "!":
                self._next()   # 消费 '!'
                return self._after_sheet(unquote_sheet_name(v))
            raise ParseError(f"带引号的表名后必须是 '!'：{v}")
        if k == "ident":
            nxt = self._peek()
            # 跨表前缀 Sheet!xxx
            if nxt and nxt[0] == "op" and nxt[1] == "!":
                self._next()
                return self._after_sheet(v)
            # 函数
            if nxt and nxt[0] == "op" and nxt[1] == "(":
                self._next()
                args = self._arglist()
                return FuncCall(v, args)
            # 单元格 / 区域（支持绝对引用 $A$1 / A$1 / $A1）
            ref = _parse_ref_part(v)
            if ref is not None:
                r0, c0, ar, ac = ref
                t2 = self._peek()
                if t2 and t2[0] == "op" and t2[1] == ":":
                    self._next()
                    k3, v3 = self._next()
                    ref2 = _parse_ref_part(v3) if k3 == "ident" else None
                    if ref2 is None:
                        raise ParseError(f"区域结束引用非法：'{v3}'")
                    r2, c2, ar2, ac2 = ref2
                    return RangeRef(None, r0, c0, r2, c2, ar, ac, ar2, ac2)
                return CellRef(None, r0, c0, ar, ac)
            raise ParseError(f"无法识别的名称：'{v}'")
        raise ParseError(f"意外标记：'{v}'")

    def _after_sheet(self, sheet: str):
        k, v = self._next()
        if k != "ident":
            raise ParseError(f"表 '{sheet}' 后缺少引用")
        nxt = self._peek()
        # Sheet!PARAM("x")
        if v.upper() == "PARAM" and nxt and nxt[0] == "op" and nxt[1] == "(":
            self._next()
            args = self._arglist()
            return _SheetParam(sheet, args)
        ref = _parse_ref_part(v)
        if ref is None:
            raise ParseError(f"表 '{sheet}' 后引用非法：'{v}'")
        r0, c0, ar, ac = ref
        t2 = self._peek()
        if t2 and t2[0] == "op" and t2[1] == ":":
            self._next()
            k3, v3 = self._next()
            ref2 = _parse_ref_part(v3) if k3 == "ident" else None
            if ref2 is None:
                raise ParseError(f"区域结束引用非法：'{v3}'")
            r2, c2, ar2, ac2 = ref2
            return RangeRef(sheet, r0, c0, r2, c2, ar, ac, ar2, ac2)
        return CellRef(sheet, r0, c0, ar, ac)

    def _arglist(self) -> list:
        args = []
        t = self._peek()
        if t and t[0] == "op" and t[1] == ")":
            self._next()
            return args
        while True:
            args.append(self._comparison())
            t = self._next()
            if t[0] == "op" and t[1] == ",":
                continue
            if t[0] == "op" and t[1] == ")":
                return args
            raise ParseError(f"参数表期望 ',' 或 ')'，实际 '{t[1]}'")


class _SheetParam:
    """SheetX!PARAM("名") —— 带表前缀的参数引用。"""

    def __init__(self, sheet: str, args: list):
        self.sheet, self.args = sheet, args


# ---------------------------------------------------------------------------
# 宿主接入接口（阶段3 由 calc_sheet 网格引擎实现）
# ---------------------------------------------------------------------------

class CellProvider:
    """网格 + 数据源的抽象接口。所有坐标 0 基。"""

    def raw_cell(self, sheet: str, row0: int, col0: int) -> Tuple[str, str]:
        """返回 (raw, kind)，kind ∈ formula/number/text/blank。越界返回 ('','blank')。"""
        raise NotImplementedError

    def param(self, sheet: str, name: str):
        """命名参数；未定义返回 None。"""
        raise NotImplementedError

    def data(self, person: str, indicator: str, year: int, month: Optional[int]) -> float:
        """DATA() 取数；失败抛 CalcDataError 子类。"""
        raise NotImplementedError

    def has_sheet(self, sheet: str) -> bool:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# evaluator
# ---------------------------------------------------------------------------

_KNOWN_FUNCS = {"SUM", "ROUND", "AVERAGE", "IF", "MIN", "MAX", "ABS", "DATA", "PARAM"}


def excel_round(x: float, n: int) -> float:
    """Excel 式四舍五入（half-up，规避二进制浮点 2.675 问题）。"""
    q = Decimal(1).scaleb(-int(n))
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))


def _to_number(v):
    """Excel 语义：数字→float；''/None→0.0；数字文本→浮点；其它文本→#VALUE!。"""
    if is_err(v):
        return v
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        return ErrVal(ERR_VALUE, f"'{v}' 不是数字")


class Engine:
    """求值器：绑定一个网格 provider，带请求级缓存与循环引用检测。

    注意（P3-6 加固注记）：`_cache` 的生命周期 = 实例 = 单次求值批次
    （UI 每次重算 `_fill` 都新建 CalcEvaluator）。勿跨编辑批次复用同一实例——
    缓存不随网格数据失效，复用会拿到旧值（含被永久记住的 ERR_CIRC）。
    """

    def __init__(self, provider: CellProvider, cur_sheet: str):
        self.provider = provider
        self.cur_sheet = cur_sheet
        self._cache = {}
        self._stack = set()

    # ---- 对外入口 ----
    def evaluate(self, raw: str):
        """求值一个公式字符串（可带或不带前导 '='）。语法错→#ERROR!。"""
        src = raw[1:] if raw.startswith("=") else raw
        try:
            node = Parser(src).parse()
        except ParseError as e:
            return ErrVal(ERR_SYNTAX, str(e))
        return self._eval(node)

    def cell_value(self, sheet: str, row0: int, col0: int):
        """引用取值（带缓存与环检测）。"""
        if not self.provider.has_sheet(sheet):
            return ErrVal(ERR_REF, f"表 {sheet} 不存在")
        key = (sheet, row0, col0)
        if key in self._cache:
            return self._cache[key]
        if key in self._stack:
            return ErrVal(ERR_CIRC, f"{sheet}!{rc_to_a1(row0, col0)} 循环引用")
        raw, kind = self.provider.raw_cell(sheet, row0, col0)
        if kind == "blank":
            v = 0.0
        elif kind == "number":
            try:
                v = float(str(raw).strip())
            except ValueError:
                v = ErrVal(ERR_VALUE)
        elif kind == "text":
            v = raw
        else:
            self._stack.add(key)
            try:
                v = self.evaluate(raw)
            finally:
                self._stack.discard(key)
        self._cache[key] = v
        return v

    # ---- 节点求值 ----
    def _eval(self, node):
        if isinstance(node, Num):
            return node.v
        if isinstance(node, Str):
            return node.v
        if isinstance(node, ErrRef):
            return ErrVal(ERR_REF)
        if isinstance(node, CellRef):
            return self.cell_value(node.sheet or self.cur_sheet, node.row0, node.col0)
        if isinstance(node, RangeRef):
            return ErrVal(ERR_VALUE, "区域引用只能用于 SUM/AVERAGE/MIN/MAX")
        if isinstance(node, UnaryOp):
            v = _to_number(self._eval(node.operand))
            if is_err(v):
                return v
            return -v if node.op == "-" else v
        if isinstance(node, BinOp):
            return self._binop(node)
        if isinstance(node, _SheetParam):
            return self._call_param(node.sheet, node.args)
        if isinstance(node, FuncCall):
            return self._call(node)
        return ErrVal(ERR_SYNTAX, "未知节点")

    def _binop(self, node: BinOp):
        op = node.op
        if op in ("=", "<>", "<", ">", "<=", ">="):
            l = self._eval(node.l)
            if is_err(l):
                return l
            r = self._eval(node.r)
            if is_err(r):
                return r
            return self._compare(op, l, r)
        l = _to_number(self._eval(node.l))
        if is_err(l):
            return l
        r = _to_number(self._eval(node.r))
        if is_err(r):
            return r
        if op == "+":
            return l + r
        if op == "-":
            return l - r
        if op == "*":
            return l * r
        if op == "/":
            if r == 0:
                return ErrVal(ERR_DIV)
            return l / r
        if op == "^":
            try:
                return float(l ** r)
            except TypeError:
                # P1-1：Py3 负底数 × 非整数指数返回 complex，float() 抛 TypeError
                # （Excel 语义：非法幂返回 #NUM!，如 =(-8)^0.5）
                return ErrVal(ERR_NUM, "负数不能开非整数次幂")
            except (OverflowError, ValueError, ZeroDivisionError):
                return ErrVal(ERR_VALUE, "幂运算溢出/非法")
        return ErrVal(ERR_SYNTAX, f"未知运算符 {op}")

    @staticmethod
    def _compare(op: str, l, r):
        # Excel：数字 < 文本；同类型直接比较
        ln, rn = isinstance(l, (int, float)), isinstance(r, (int, float))
        if ln and not rn:
            c = -1
        elif rn and not ln:
            c = 1
        else:
            lf, rf = _to_number(l), _to_number(r)
            if not is_err(lf) and not is_err(rf) and ln and rn:
                c = (lf > rf) - (lf < rf)
            else:
                ls, rs = str(l), str(r)
                c = (ls > rs) - (ls < rs)
        res = {"=": c == 0, "<>": c != 0, "<": c < 0,
               ">": c > 0, "<=": c <= 0, ">=": c >= 0}[op]
        return 1.0 if res else 0.0

    # ---- 函数 ----
    def _call(self, node: FuncCall):
        name = node.name
        if name not in _KNOWN_FUNCS:
            return ErrVal(ERR_NAME, f"未知函数 {name}")
        if name == "IF":
            return self._call_if(node.args)
        if name == "PARAM":
            return self._call_param(self.cur_sheet, node.args)
        if name in ("SUM", "AVERAGE", "MIN", "MAX"):
            # 聚合函数：区域参数展开，忽略文本/空格
            nums = self._flatten_numbers(node.args)
            for v in nums:
                if is_err(v):
                    return v
            vals = [v for v in nums if not is_err(v)]
            if name == "SUM":
                return float(sum(vals))
            if name == "AVERAGE":
                return ErrVal(ERR_DIV, "无可用数值") if not vals else float(sum(vals) / len(vals))
            if name == "MIN":
                return 0.0 if not vals else float(min(vals))
            return 0.0 if not vals else float(max(vals))
        args = [self._eval(a) for a in node.args]
        for a in args:
            if is_err(a):
                return a
        if name == "DATA":
            return self._call_data(args)
        if name == "ROUND":
            if not 1 <= len(args) <= 2:
                return ErrVal(ERR_VALUE, "ROUND 需要 1~2 个参数")
            n = int(args[1]) if len(args) == 2 else 0
            return excel_round(args[0], n)
        if name == "ABS":
            return abs(args[0])
        return ErrVal(ERR_NAME, f"未知函数 {name}")

    def _call_if(self, args):
        if len(args) not in (2, 3):
            return ErrVal(ERR_VALUE, "IF 需要 2~3 个参数")
        cond = self._eval(args[0])
        if is_err(cond):
            return cond
        truth = _to_number(cond)
        if is_err(truth):
            return truth
        return self._eval(args[1]) if truth != 0 else (
            self._eval(args[2]) if len(args) == 3 else 0.0)

    def _call_param(self, sheet: str, args):
        if len(args) != 1:
            return ErrVal(ERR_VALUE, "PARAM 需要 1 个参数")
        v = self._eval(args[0])
        if is_err(v):
            return v
        if not isinstance(v, str):
            return ErrVal(ERR_VALUE, "PARAM 参数须为名称文本")
        target = sheet or self.cur_sheet
        if not self.provider.has_sheet(target):
            return ErrVal(ERR_REF, f"表 {target} 不存在")
        pv = self.provider.param(target, v)
        if pv is None:
            return ErrVal(ERR_NAME, f"参数 '{v}' 未定义")
        return pv

    def _call_data(self, args):
        if len(args) not in (3, 4):
            return ErrVal(ERR_VALUE, "DATA 需要 3~4 个参数")
        person, indicator = args[0], args[1]
        if not isinstance(person, str) or not isinstance(indicator, str):
            return ErrVal(ERR_VALUE, "DATA 前两个参数须为文本")
        year = _to_number(args[2])
        if is_err(year):
            return year
        year = int(year)
        month: Optional[int] = None
        if len(args) == 4:
            if isinstance(args[3], str) and args[3].strip() in ("", "全年"):
                month = None
            else:
                mnum = _to_number(args[3])
                if is_err(mnum):
                    return mnum
                if mnum != int(mnum) or not 1 <= int(mnum) <= 12:
                    return ErrVal(ERR_VALUE, f"月份非法：{args[3]}")
                month = int(mnum)
        try:
            return float(self.provider.data(person, indicator, year, month))
        except CalcDataFailure as e:
            if getattr(e, "unexpected", False):
                return _wrap_unexpected(e, person, indicator, year, month)
            # 业务错误（未知指标 / 姓名重名 / 月份非法 / 指标循环）→ 保持既有 #REF!
            return ErrVal(ERR_REF, str(e))
        except Exception as e:
            # 非预期：表结构缺失、驱动错误、除零溢出之外的任何崩溃。
            # 必须与 #REF! 区分开，并留下可排障的日志。
            return _wrap_unexpected(e, person, indicator, year, month)

    def _flatten_numbers(self, args) -> list:
        """参数展开：区域逐格取值（文本/空格跳过，公式格求值），散值直接收。"""
        out = []
        for a in args:
            if isinstance(a, RangeRef):
                sheet = a.sheet or self.cur_sheet
                if not self.provider.has_sheet(sheet):
                    out.append(ErrVal(ERR_REF, f"表 {sheet} 不存在"))
                    return out
                for r in range(a.r1, a.r2 + 1):
                    for c in range(a.c1, a.c2 + 1):
                        raw, kind = self.provider.raw_cell(sheet, r, c)
                        if kind == "blank" or kind == "text":
                            continue
                        out.append(self.cell_value(sheet, r, c))
            else:
                v = self._eval(a)
                if isinstance(v, str):
                    num = _to_number(v)
                    if is_err(num):
                        continue  # 聚合忽略文本
                    v = num
                out.append(v)
        return out
