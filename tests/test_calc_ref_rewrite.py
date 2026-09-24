"""calc_ref_rewrite（引用重写）单元测试 — 纯 Python。

覆盖：插入扩张 / 删除收缩 / 命中删除→#REF! / 绝对不动 / 跨表不动 / 复制填充平移。
运行：python tests/test_calc_ref_rewrite.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine.calc_ref_rewrite import shift_refs, translate_refs  # noqa: E402


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

    # ===== 防御：解析失败原样返回 =====
    check("语法错原样返回", shift_refs("=1+", "row", 0, 1, insert=True), "=1+")

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
