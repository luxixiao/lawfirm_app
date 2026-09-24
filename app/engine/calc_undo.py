"""计算表命令栈（阶段2 撤销/重做，G1）。

设计要点（见 docs/calc_sheet_ux_implementation_plan.md §2.6 与 G0/G1 审核修订）：
- 命令 = 「content 逆操作」单元：apply/revert 都**返回新 content，绝不就地改输入**（B1）。
- 所有快照（before/after）构造时即 `deepcopy`，避免与实时 content 共享内层引用（B1/B4/B5）。
- 会话内内存栈：不写库、不跨机（复用同一写回路径 `_recalc_fill→save_content`）。
- 单格编辑=EditCellCommand；参数=ParamCommand；粘贴/清空/生长行列=BulkCommand（整 content 深拷贝快照，
  天然覆盖 rows/cols 计数与多格，避免差量漏还原，见 B2/B4）。
- StructuralCommand（插/删行列）按 B6 拆到阶段3 再做（依赖 calc_sheet.insert/delete_* 纯函数）。

命令接口（Protocol 式）：
    label: str
    apply(content) -> content     # 返回新 content
    revert(content) -> content    # 返回撤销后的 content
    anchor() -> Optional[(r,c)]   # 撤销/重做后建议把焦点移回的格（B8/G1-14）
"""
from __future__ import annotations

import copy
import dataclasses
from typing import Dict, List, Optional, Tuple

Cell = Dict[str, object]          # {"raw":..., "kind":...}
Content = Dict[str, object]       # calc_sheet.content 单 JSON


def snapshot(content: Content) -> Content:
    """整 content 深拷贝（BulkCommand 快照用）。"""
    return copy.deepcopy(content)


# ---------------------------------------------------------------------------
# 纯函数：单格 set / 批量 patch（均返回新 content，不就地改）
# ---------------------------------------------------------------------------

def _set_cell(content: Content, key: str, cell: Optional[Cell]) -> Content:
    new = dict(content)
    cells = dict(content.get("cells") or {})
    if cell is None:
        cells.pop(key, None)
    else:
        cells[key] = copy.deepcopy(cell)
    new["cells"] = cells
    return new


def _patch_cells(content: Content, mapping: Dict[str, Optional[Cell]]) -> Content:
    """批量设置/删除格，返回新 content。mapping: key -> cell 或 None（删除）。"""
    new = dict(content)
    cells = dict(content.get("cells") or {})
    for k, v in mapping.items():
        if v is None:
            cells.pop(k, None)
        else:
            cells[k] = copy.deepcopy(v)
    new["cells"] = cells
    return new


def _parse_key(key: str) -> Tuple[int, int]:
    r, c = key.split(",")
    return int(r), int(c)


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class EditCellCommand:
    """单格编辑：before/after 为 cell dict（或 None=该格原为空/清为空）。"""
    key: str
    before: Optional[Cell]
    after: Optional[Cell]

    def apply(self, content: Content) -> Content:
        return _set_cell(content, self.key, self.after)

    def revert(self, content: Content) -> Content:
        return _set_cell(content, self.key, self.before)

    def anchor(self) -> Optional[Tuple[int, int]]:
        return _parse_key(self.key)


@dataclasses.dataclass
class ParamCommand:
    """本表命名参数修改。"""
    before: Dict[str, object]
    after: Dict[str, object]

    def apply(self, content: Content) -> Content:
        new = dict(content)
        new["params"] = copy.deepcopy(self.after)
        return new

    def revert(self, content: Content) -> Content:
        new = dict(content)
        new["params"] = copy.deepcopy(self.before)
        return new

    def anchor(self) -> Optional[Tuple[int, int]]:
        return None


@dataclasses.dataclass
class BulkCommand:
    """粘贴 / 清空 / 生长行列等多格或结构变动：整 content 前后快照。

    用整 content 深拷贝而非差量，彻底避免 B4「差量漏还原 rows/cols 扩张、
    漏删被覆盖空格」与 B1 引用共享问题。
    """
    before: Content
    after: Content
    anchor_key: Optional[str] = None

    def apply(self, content: Content) -> Content:
        return copy.deepcopy(self.after)

    def revert(self, content: Content) -> Content:
        return copy.deepcopy(self.before)

    def anchor(self) -> Optional[Tuple[int, int]]:
        if not self.anchor_key:
            return None
        return _parse_key(self.anchor_key)


# ---------------------------------------------------------------------------
# 命令栈（会话内内存）
# ---------------------------------------------------------------------------

class CommandStack:
    """撤销/重做栈。undo/redo 返回命令对象（None=空栈 no-op，见 B7）。"""

    def __init__(self) -> None:
        self._undo: List[object] = []
        self._redo: List[object] = []

    def push(self, cmd) -> None:
        self._undo.append(cmd)
        self._redo.clear()  # 新操作清空重做分支

    def undo(self):
        if not self._undo:
            return None  # B7：空栈 no-op
        cmd = self._undo.pop()
        self._redo.append(cmd)
        return cmd

    def redo(self):
        if not self._redo:
            return None  # B7：空栈 no-op
        cmd = self._redo.pop()
        self._undo.append(cmd)
        return cmd

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def clear(self) -> None:
        """切换表/重新加载时清空（会话内、按表隔离）。"""
        self._undo.clear()
        self._redo.clear()
