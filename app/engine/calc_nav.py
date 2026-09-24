"""计算表键盘导航纯逻辑（与 UI 严格分离，可无头单测）。

坐标约定：r/c 均为 0 基；rows/cols 为表范围（>=0）。
occupied：可迭代的 "r,c" 字符串集合，表示有数据的单元格。
"""
from __future__ import annotations

from typing import Iterable


def _in_bounds(r: int, c: int, rows: int, cols: int) -> bool:
    return 0 <= r < rows and 0 <= c < cols


def jump_to_boundary(occupied: Iterable[str], rows: int, cols: int,
                     r: int, c: int, dr: int, dc: int):
    """沿 (dr,dc) 方向跳到数据区边界（类 Excel Ctrl+方向）。

    - 起点有数据：跳到连续数据块末端（最后一个与起点连通的有数据格）。
    - 起点为空：跳到下一个数据块之前的最后一个空格；若方向再无数据，跳到表边界。
    越界则停在表范围边界；表为空(rows/cols<=0)原样夹回边界。
    """
    occ = set(occupied)
    if rows <= 0 or cols <= 0:
        cr = max(0, min(r, rows - 1)) if rows > 0 else 0
        cc = max(0, min(c, cols - 1)) if cols > 0 else 0
        return cr, cc
    if not _in_bounds(r, c, rows, cols):
        return max(0, min(r, rows - 1)), max(0, min(c, cols - 1))
    start_occ = f"{r},{c}" in occ
    nr, nc = r, c
    if start_occ:
        while True:
            tr, tc = nr + dr, nc + dc
            if not _in_bounds(tr, tc, rows, cols):
                break
            if f"{tr},{tc}" in occ:
                nr, nc = tr, tc
            else:
                break
    else:
        while True:
            tr, tc = nr + dr, nc + dc
            if not _in_bounds(tr, tc, rows, cols):
                break
            if f"{tr},{tc}" in occ:
                break
            nr, nc = tr, tc
    return nr, nc


def nav_step(r: int, c: int, rows: int, cols: int, key: str, shift: bool = False):
    """非编辑态 Tab/Enter 移动目标（导航态仅移动，无提交）。

    key: 'tab' | 'enter'。返回 (new_r, new_c)，越界夹在边界（不换行扩展表格）。
    """
    if rows <= 0 or cols <= 0:
        return (max(0, min(r, rows - 1)) if rows > 0 else 0,
                max(0, min(c, cols - 1)) if cols > 0 else 0)
    r = max(0, min(r, rows - 1))
    c = max(0, min(c, cols - 1))
    if key == "tab":
        nc = c + (-1 if shift else 1)
        nc = 0 if nc < 0 else (cols - 1 if nc >= cols else nc)
        return r, nc
    if key == "enter":
        nr = r + (-1 if shift else 1)
        nr = 0 if nr < 0 else (rows - 1 if nr >= rows else nr)
        return nr, c
    return r, c
