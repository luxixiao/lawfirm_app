"""计算表键盘导航纯逻辑单测（无头，不依赖 Qt）。

覆盖：jump_to_boundary（Ctrl+方向跳数据边界）、nav_step（Tab/Enter 移动目标）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine.calc_nav import jump_to_boundary, nav_step  # noqa: E402


def _run():
    fails = []

    def check(name, got, exp):
        if got != exp:
            fails.append(f"{name}: got={got!r} exp={exp!r}")

    # ---------------- jump_to_boundary ----------------
    occ = {"0,0", "0,1", "0,2"}  # 仅第0行有数据
    # 起点有数据，向下：col0 仅 (0,0) 有，下一格空 -> 停在 (0,0)
    check("down@0,0 occ", jump_to_boundary(occ, 5, 3, 0, 0, 1, 0), (0, 0))
    # 起点空，向下：col0 再无数据 -> 表尾 (4,0)
    check("down@2,0 empty", jump_to_boundary(occ, 5, 3, 2, 0, 1, 0), (4, 0))
    # 起点有数据，向右：连续 (0,0)(0,1)(0,2) -> 末 (0,2)
    check("right@0,0 occ", jump_to_boundary(occ, 5, 3, 0, 0, 0, 1), (0, 2))
    # 起点有数据在块末，向右越界 -> (0,2)
    check("right@0,2 occ", jump_to_boundary(occ, 5, 3, 0, 2, 0, 1), (0, 2))
    # 起点空，向右：col2 无数据 -> 表右 (3,2)
    check("right@3,1 empty", jump_to_boundary(occ, 5, 3, 3, 1, 0, 1), (3, 2))
    # 起点有数据，向左越界 -> (0,0)
    check("left@0,0 occ", jump_to_boundary(occ, 5, 3, 0, 0, 0, -1), (0, 0))
    # 起点空，向左：col2 全空 -> 左边界 (3,0)
    check("left@3,2 empty", jump_to_boundary(occ, 5, 3, 3, 2, 0, -1), (3, 0))
    # 起点空，向上：col0 上方 (0,0) 有数据 -> 停在数据块前的最后一个空白 (1,0)（Excel 语义）
    check("up@3,0 empty", jump_to_boundary(occ, 5, 3, 3, 0, -1, 0), (1, 0))
    # 空表边界夹回
    check("empty bounds", jump_to_boundary(occ, 0, 0, 2, 2, 1, 0), (0, 0))

    # 跨行数据块：col0 有 (1,0)(2,0)(3,0)
    occ2 = {"1,0", "2,0", "3,0"}
    check("down@1,0 block", jump_to_boundary(occ2, 6, 2, 1, 0, 1, 0), (3, 0))
    check("up@3,0 block", jump_to_boundary(occ2, 6, 2, 3, 0, -1, 0), (1, 0))
    check("down@4,0 below", jump_to_boundary(occ2, 6, 2, 4, 0, 1, 0), (5, 0))

    # ---------------- nav_step ----------------
    check("tab@0,0", nav_step(0, 0, 5, 3, "tab", False), (0, 1))
    check("tab@0,2 edge", nav_step(0, 2, 5, 3, "tab", False), (0, 2))
    check("shift-tab@0,0", nav_step(0, 0, 5, 3, "tab", True), (0, 0))
    check("enter@0,0", nav_step(0, 0, 5, 3, "enter", False), (1, 0))
    check("enter@4,0 edge", nav_step(4, 0, 5, 3, "enter", False), (4, 0))
    check("shift-enter@0,0", nav_step(0, 0, 5, 3, "enter", True), (0, 0))
    check("bad key", nav_step(2, 1, 5, 3, "x", False), (2, 1))
    check("empty bounds nav", nav_step(2, 2, 0, 0, "tab", False), (0, 0))

    if fails:
        print("FAILS:")
        for f in fails:
            print("  ", f)
        return 1
    print("OK: 19 项导航逻辑断言全部通过")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_run())
