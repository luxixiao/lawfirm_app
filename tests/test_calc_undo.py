"""calc_undo（阶段2 命令栈，G1）单元测试 — 纯 Python，无头。

覆盖（见 docs/calc_sheet_ux_implementation_plan.md §2.6 与 G0/G1 审核修订）：
- EditCellCommand / ParamCommand / BulkCommand 的 apply/revert 逆操作正确性。
- B1：apply/revert 返回新 content，绝不就地改入参（含内层 cells / params 引用）。
- BulkCommand 整 content 快照：行列扩张 + 回滚、被覆盖空格的还原。
- B7：CommandStack 推送 / undo / redo / can_undo / can_redo，空栈 no-op 返回 None，
     新操作清空重做分支。
- 连续编辑 → undo N → redo N 的整链路一致性。
运行：python tests/test_calc_undo.py
"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine.calc_undo import (  # noqa: E402
    CommandStack, EditCellCommand, ParamCommand, BulkCommand, snapshot)


OK, FAILS = 0, []


def check(label, got, want):
    global OK
    if got == want:
        OK += 1
    else:
        FAILS.append(f"{label}: got={got!r} want={want!r}")


BASE = {"rows": 5, "cols": 3, "cells": {"0,0": {"raw": "=A2", "kind": "formula"}}}


def test_editcell_apply_revert():
    # 空 → 写入
    cmd = EditCellCommand("1,1", before=None, after={"raw": "5", "kind": "number"})
    a = cmd.apply(copy.deepcopy(BASE))
    check("EditCell 写入后存在", a["cells"].get("1,1"), {"raw": "5", "kind": "number"})
    r = cmd.revert(a)
    check("EditCell 还原后清空", "1,1" in r["cells"], False)
    # 已有 → 改写
    cmd2 = EditCellCommand("0,0", before={"raw": "=A2", "kind": "formula"},
                           after={"raw": "=A3", "kind": "formula"})
    a2 = cmd2.apply(copy.deepcopy(BASE))
    check("EditCell 改写内容", a2["cells"]["0,0"], {"raw": "=A3", "kind": "formula"})
    check("EditCell 还原原值", cmd2.revert(a2)["cells"]["0,0"],
          {"raw": "=A2", "kind": "formula"})


def test_editcell_no_mutate(B1):  # noqa: N803
    cmd = EditCellCommand("1,1", before=None, after={"raw": "5", "kind": "number"})
    base = copy.deepcopy(BASE)
    a = cmd.apply(base)
    # 入参 base 不应被就地修改（cells 仍只有 0,0）
    check("B1: apply 不改入参 cells", base["cells"], {"0,0": {"raw": "=A2", "kind": "formula"}})
    check("B1: apply 返回新对象", a is not base, True)
    # revert 也不改 a
    r = cmd.revert(a)
    check("B1: revert 不改 a", a["cells"].get("1,1"), {"raw": "5", "kind": "number"})
    check("B1: revert 返回新对象", r is not a, True)


def test_param_apply_revert():
    cmd = ParamCommand(before={}, after={"rate": 0.1, "name": "税率"})
    base = copy.deepcopy(BASE)
    a = cmd.apply(base)
    check("Param 应用后参数", a.get("params"), {"rate": 0.1, "name": "税率"})
    check("Param 入参不改", base.get("params"), None)
    check("Param 还原=before(空)", cmd.revert(a).get("params"), {})


def test_bulk_rows_cols_expand_and_rollback():
    before = copy.deepcopy(BASE)  # rows=5, cols=3
    after = copy.deepcopy(BASE)
    after["rows"] = 10
    after["cols"] = 6
    after["cells"]["9,5"] = {"raw": "x", "kind": "text"}
    cmd = BulkCommand(before=before, after=after)
    a = cmd.apply(copy.deepcopy(before))
    check("Bulk 行列扩张", (a["rows"], a["cols"]), (10, 6))
    check("Bulk 新格存在", a["cells"].get("9,5"), {"raw": "x", "kind": "text"})
    r = cmd.revert(a)
    check("Bulk 回滚行列", (r["rows"], r["cols"]), (5, 3))
    check("Bulk 回滚删除新格", "9,5" in r["cells"], False)


def test_bulk_blank_restored():
    # 覆盖写入会把原先非空格改为空（清空）→ 撤销应还原原值
    before = {"rows": 2, "cols": 2, "cells": {"0,0": {"raw": "a", "kind": "text"}}}
    after = {"rows": 2, "cols": 2, "cells": {}}  # 0,0 被清空
    cmd = BulkCommand(before=before, after=after)
    r = cmd.revert(cmd.apply(copy.deepcopy(before)))
    check("Bulk 还原被清空格", r["cells"].get("0,0"), {"raw": "a", "kind": "text"})


def test_bulk_no_mutate():
    before = copy.deepcopy(BASE)
    after = copy.deepcopy(BASE)
    after["cells"]["2,2"] = {"raw": "z", "kind": "text"}
    cmd = BulkCommand(before=before, after=after)
    a = cmd.apply(before)  # 故意传 before，验证 apply 内部 deepcopy
    check("Bulk B1: 入参 before 未被改", "2,2" in before["cells"], False)
    check("Bulk B1: 返回为新对象", a is not after, True)


def test_stack_basic(B7):  # noqa: N803
    st = CommandStack()
    check("B7: 初始不可撤销", st.can_undo(), False)
    check("B7: 初始不可重做", st.can_redo(), False)
    check("B7: 空栈 undo=None", st.undo() is None, True)
    check("B7: 空栈 redo=None", st.redo() is None, True)
    c1 = EditCellCommand("0,0", before={"raw": "=A2", "kind": "formula"},
                         after={"raw": "1", "kind": "number"})
    st.push(c1)
    check("B7: 入栈后可撤销", st.can_undo(), True)
    got = st.undo()
    check("B7: undo 返回命令", got is c1, True)
    check("B7: 撤销后不可撤销", st.can_undo(), False)
    check("B7: 撤销后可重做", st.can_redo(), True)
    got2 = st.redo()
    check("B7: redo 返回原命令", got2 is c1, True)
    check("B7: 重做后再可撤销", st.can_undo(), True)


def test_stack_order():
    st = CommandStack()
    c1 = EditCellCommand("0,0", before=None, after={"raw": "1", "kind": "number"})
    c2 = EditCellCommand("0,1", before=None, after={"raw": "2", "kind": "number"})
    st.push(c1)
    st.push(c2)
    check("栈序 undo1=后入", st.undo() is c2, True)
    check("栈序 undo2=先入", st.undo() is c1, True)
    check("栈空 undo=None", st.undo() is None, True)
    check("重做1=先入", st.redo() is c1, True)
    check("重做2=后入", st.redo() is c2, True)


def test_stack_push_clears_redo():
    st = CommandStack()
    c1 = EditCellCommand("0,0", before=None, after={"raw": "1", "kind": "number"})
    c2 = EditCellCommand("0,1", before=None, after={"raw": "2", "kind": "number"})
    st.push(c1)
    st.undo()
    check("撤销后可重做", st.can_redo(), True)
    st.push(c2)  # 新操作清空重做分支
    check("新操作清空重做", st.can_redo(), False)


def test_continuous_undo_redo():
    # 连续两次编辑 → undo 两次 → 与原状一致 → redo 两次 → 与终态一致
    content = copy.deepcopy(BASE)
    cmds = [
        EditCellCommand("1,1", before=None, after={"raw": "5", "kind": "number"}),
        EditCellCommand("2,2", before=None, after={"raw": "7", "kind": "number"}),
    ]
    states = [content]
    for cmd in cmds:
        states.append(cmd.apply(states[-1]))
    final = states[-1]
    check("连续编辑终态含两格",
          (final["cells"].get("1,1"), final["cells"].get("2,2")),
          ({"raw": "5", "kind": "number"}, {"raw": "7", "kind": "number"}))

    st = CommandStack()
    for cmd in cmds:
        st.push(cmd)

    # undo 两次，逐步回退
    a = cmds[1].revert(final)            # 去掉 2,2
    check("undo1 去掉末格", "2,2" in a["cells"], False)
    check("undo1 保留前格", a["cells"].get("1,1"), {"raw": "5", "kind": "number"})
    b = cmds[0].revert(a)                # 去掉 1,1
    check("undo2 回到原状", b["cells"], {"0,0": {"raw": "=A2", "kind": "formula"}})

    # redo 两次，重建终态
    c = cmds[0].apply(b)
    d = cmds[1].apply(c)
    check("redo 重建终态",
          (d["cells"].get("1,1"), d["cells"].get("2,2")),
          ({"raw": "5", "kind": "number"}, {"raw": "7", "kind": "number"}))


def test_snapshot_is_deepcopy():
    base = copy.deepcopy(BASE)
    s = snapshot(base)
    s["cells"]["0,0"] = {"raw": "hacked", "kind": "text"}
    check("snapshot 深拷贝隔离", base["cells"]["0,0"], {"raw": "=A2", "kind": "formula"})


def main() -> int:
    test_editcell_apply_revert()
    test_editcell_no_mutate(None)
    test_param_apply_revert()
    test_bulk_rows_cols_expand_and_rollback()
    test_bulk_blank_restored()
    test_bulk_no_mutate()
    test_stack_basic(None)
    test_stack_order()
    test_stack_push_clears_redo()
    test_continuous_undo_redo()
    test_snapshot_is_deepcopy()

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
