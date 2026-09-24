"""calc_ref_rewrite（引用重写）单元测试 — 纯 Python。

覆盖：插入扩张 / 删除收缩 / 命中删除→#REF! / 绝对不动 / 跨表不动 / 复制填充平移。
运行：python tests/test_calc_ref_rewrite.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine.calc_ref_rewrite import (  # noqa: E402
    shift_refs, translate_refs, _walk, _make_shift_T)
from app.engine.calc_formula import Parser, CellRef, ErrRef


OK, FAILS = 0, []


def check(label, got, want):
    global OK
    if got == want:
        OK += 1
    else:
        FAILS.append(f"{label}: got={got!r} want={want!r}")


def main() -> int:
    # ===== shift_refs：插入（insert=True）=====
    # 区域内部插入 → 扩张
    check("插入行(第10行) SUM(A2:A50)→A51",
          shift_refs("=SUM(A2:A50)", "row", 9, 1, insert=True), "=SUM(A2:A51)")
    # 插入点在区域近端(at==r1)→整体平移不扩张（R2）
    check("插入行(at=1) SUM(A2:A10)→A3:A11",
          shift_refs("=SUM(A2:A10)", "row", 1, 1, insert=True), "=SUM(A3:A11)")
    # 绝对行+列不动，相对行平移
    check("插行 $A$1+B1→$A$1+B2",
          shift_refs("=$A$1+B1", "row", 0, 1, insert=True), "=$A$1+B2")
    # 跨表引用不平移
    check("插行 Sheet2!A1+A1→Sheet2!A1+A2",
          shift_refs("=Sheet2!A1+A1", "row", 0, 1, insert=True), "=Sheet2!A1+A2")
    # 列插入：相对列平移、绝对列不动
    check("插列(0) A$1+B$1→B$1+C$1",
          shift_refs("=A$1+B$1", "col", 0, 1, insert=True), "=B$1+C$1")

    # ===== shift_refs：删除（insert=False）=====
    # 区域内部删除 → 收缩
    check("删行(1) SUM(A1:A3)→A1:A2",
          shift_refs("=SUM(A1:A3)", "row", 1, 1, insert=False), "=SUM(A1:A2)")
    # 整格引用落进删除区间 → #REF!
    check("删行(1) A2→#REF!",
          shift_refs("=A2", "row", 1, 1, insert=False), "=#REF!")
    # 区域端点落进删除区间 → 整片 #REF!
    check("删行(2) SUM(A1:A3)→#REF!",
          shift_refs("=SUM(A1:A3)", "row", 2, 1, insert=False), "=SUM(#REF!)")
    # 删除点之下的引用上移
    check("删行(3) A1+B5→A1+B4",
          shift_refs("=A1+B5", "row", 3, 1, insert=False), "=A1+B4")

    # ===== translate_refs：复制/填充平移 =====
    check("右移1列 B2*C2→C2*D2",
          translate_refs("=B2*C2", 0, 1), "=C2*D2")
    check("列绝对 $B2 右移不动",
          translate_refs("=$B2", 0, 1), "=$B2")
    check("下行1行 $B2→$B3",
          translate_refs("=$B2", 1, 0), "=$B3")
    check("行绝对 A$1 下移不动",
          translate_refs("=A$1", 1, 0), "=A$1")
    check("跨表不平移 + 自引用平移 Sheet2!A1+A1→Sheet2!A1+B2",
          translate_refs("=Sheet2!A1+A1", 1, 1), "=Sheet2!A1+B2")
    check("区域平移 SUM(B2:C3)→SUM(C3:D4)",
          translate_refs("=SUM(B2:C3)", 1, 1), "=SUM(C3:D4)")
    # 非公式 / 纯数字原样
    check("纯数字不误改", translate_refs("123", 1, 1), "123")
    check("含 #REF! 公式保持", translate_refs("=#REF!+A1", 1, 1), "=#REF!+B2")

    # ===== 负位移→越界引用变 #REF!（G0-1 修复）=====
    check("左移越界 A1→#REF!", translate_refs("=A1", 0, -1), "=#REF!")
    check("上移越界 A1→#REF!", translate_refs("=A1", -1, 0), "=#REF!")
    check("左移越界 范围→#REF!", translate_refs("=SUM(A1:A3)", 0, -1), "=SUM(#REF!)")
    # 合法负位移仍正常平移（不误伤）
    check("左移1列 B2*C2→A2*B2", translate_refs("=B2*C2", 0, -1), "=A2*B2")

    # ===== 阶段4 G4：复制/填充的块位移（translate_refs 复用阶段0）=====
    # 源 B2*C2 复制到目标 D4（右2下2）→ D4*E4（计划 §323 验证例）
    check("块位移 右2下2 B2*C2→D4*E4",
          translate_refs("=B2*C2", 2, 2), "=D4*E4")
    check("块位移 右2下2 SUM(B2:C3)→SUM(D4:E5)",
          translate_refs("=SUM(B2:C3)", 2, 2), "=SUM(D4:E5)")
    # 绝对列在块位移中不动（仅行平移）
    check("块位移 含列绝对 $B2 右2下2→$B4",
          translate_refs("=$B2", 2, 2), "=$B4")
    # 跨表引用不平移 + 本地引用按块位移（右2下2：A1→C3）
    check("块位移 跨表+自引用 Sheet2!A1+A1→Sheet2!A1+C3",
          translate_refs("=Sheet2!A1+A1", 2, 2), "=Sheet2!A1+C3")
    # 块位移中本地端越界→#REF!（源在 A 列、左移整块越界）
    check("块位移 左移越界 A1→#REF!",
          translate_refs("=A1", 0, -1), "=#REF!")

    # ===== 防御：解析失败原样返回 =====
    check("语法错原样返回", shift_refs("=1+", "row", 0, 1, insert=True), "=1+")

    # ===== G0-5 完善：_make_shift_T 纯函数化（不得原地改写输入 CellRef）=====
    def _shift_node(formula, T):
        src = formula[1:] if formula.startswith("=") else formula
        n = Parser(src).parse()
        assert isinstance(n, CellRef), f"{formula} 应解析为 CellRef"
        before = n.row0
        out = _walk(n, T)
        return n, before, out

    T_ins = _make_shift_T("row", 0, 1, insert=True)
    n1, b1, o1 = _shift_node("=A2", T_ins)
    check("G0-5 插入: 原节点未改写", n1.row0, b1)
    check("G0-5 插入: 新坐标+1", o1.row0, b1 + 1)
    check("G0-5 插入: 返回新对象", o1 is not n1, True)

    T_del = _make_shift_T("row", 1, 1, insert=False)
    n2, b2, o2 = _shift_node("=A2", T_del)
    check("G0-5 删除: 原节点未改写", n2.row0, b2)
    check("G0-5 删除命中→ErrRef", isinstance(o2, ErrRef), True)

    # 绝对行（A$2）：行不平移、原节点不改写
    T_abs_row = _make_shift_T("row", 0, 1, insert=True)
    n3, b3, o3 = _shift_node("=A$2", T_abs_row)
    check("G0-5 绝对行: 原节点未改写", n3.row0, b3)
    check("G0-5 绝对行: 坐标不变", o3.row0, b3)
    # 绝对列（$A2）：列不平移，但行是相对 → 应平移；原节点不改写
    n4, b4, o4 = _shift_node("=$A2", T_ins)
    check("G0-5 绝对列: 原节点未改写", n4.row0, b4)
    check("G0-5 绝对列: 相对行仍平移", o4.row0, b4 + 1)

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
