"""跨表引用生成（阶段5 C 方案批次4）单元测试 — 纯逻辑 + 离屏对话框。

覆盖 `app.ui.calc_dialogs.build_sheet_ref` / `list_sheet_names` / `SheetRefDialog`：
- **引号由引擎决定**：普通中文名不加引号，数字开头 / 含 `-` `.` 的自动加单引号，
  表名里的 `'` 按 Excel 约定转义成 `''`；
- 小写地址与区域规范化（走 parse→render 往返，保证与引擎规范形一致）；
- 绝对引用 `$A$1`；
- 非法参数返回空串（不做半截写入）；
- `list_sheet_names` 排除当前表（避免自引用）；
- 对话框：预览与确认结果一致，非法地址不 accept。

运行：python tests/test_calc_sheet_ref.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui import calc_dialogs as cd  # noqa: E402
from app.ui.calc_dialogs import SheetRefDialog, build_sheet_ref, list_sheet_names  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def main() -> int:
    # ===== 纯逻辑：build_sheet_ref =====
    check("普通中文名不加引号", build_sheet_ref("辅助表", "A1") == "=辅助表!A1",
          f"got={build_sheet_ref('辅助表', 'A1')}")
    check("数字开头自动加引号", build_sheet_ref("2026-01", "A1") == "='2026-01'!A1",
          f"got={build_sheet_ref('2026-01', 'A1')}")
    check("含中文+数字加引号", build_sheet_ref("2025分成", "A1") == "='2025分成'!A1",
          f"got={build_sheet_ref('2025分成', 'A1')}")
    check("小写地址规范化", build_sheet_ref("2025分成", "b2") == "='2025分成'!B2",
          f"got={build_sheet_ref('2025分成', 'b2')}")
    check("区域引用", build_sheet_ref("2026-01", "a1:b2") == "='2026-01'!A1:B2",
          f"got={build_sheet_ref('2026-01', 'a1:b2')}")
    check("区域也带引号一次", build_sheet_ref("2025分成", "A1:B2") == "='2025分成'!A1:B2",
          f"got={build_sheet_ref('2025分成', 'A1:B2')}")
    check("表名内引号转义", build_sheet_ref("a'b", "A1") == "='a''b'!A1",
          f"got={build_sheet_ref(chr(39).join(['a', 'b']), 'A1')}")
    check("绝对引用 $A$1", build_sheet_ref("2026-01", "A1", True, True)
          == "='2026-01'!$A$1", f"got={build_sheet_ref('2026-01', 'A1', True, True)}")
    check("只锁列", build_sheet_ref("辅助表", "A1", True, False) == "=辅助表!$A1",
          f"got={build_sheet_ref('辅助表', 'A1', True, False)}")
    check("只锁行", build_sheet_ref("辅助表", "A1", False, True) == "=辅助表!A$1",
          f"got={build_sheet_ref('辅助表', 'A1', False, True)}")
    check("用户手输 $ 也能吃", build_sheet_ref("辅助表", "$A$1") == "=辅助表!A1",
          f"got={build_sheet_ref('辅助表', '$A$1')}")

    # --- 非法参数一律空串（UI 层据此拒绝 accept，绝不写半截公式）---
    check("空表名", build_sheet_ref("", "A1") == "")
    check("空地址", build_sheet_ref("辅助表", "") == "")
    check("非法地址", build_sheet_ref("辅助表", "ZZZ") == "")
    check("半截区域", build_sheet_ref("辅助表", "A1:") == "")
    # 区域反序由引擎 RangeRef 自行归一化（B2:A1 → A1:B2），与 Excel 一致
    check("区域反序被引擎归一化", build_sheet_ref("辅助表", "B2:A1") == "=辅助表!A1:B2",
          f"got={build_sheet_ref('辅助表', 'B2:A1')}")
    # --- Excel 引用硬边界（列≤XFD=16384、行≤1048576，超出 Excel 打不开）---
    # 注：引擎公式解析器 calc_formula._CELL_RE 行仅接受 1-5 位数字，6/7 位行
    # 本就走不到 parse（ParseError 返回 ""），故 build_sheet_ref 层用引擎可达的
    # 5 位行测列边界；Excel 满行边界由纯函数 _a1_in_bounds 单独覆盖。
    check("边界内 XFD99999 合法", build_sheet_ref("t", "XFD99999") != "",
          f"got={build_sheet_ref('t', 'XFD99999')}")
    check("区间两端都合法 A1:XFD99999", build_sheet_ref("t", "A1:XFD99999") != "",
          f"got={build_sheet_ref('t', 'A1:XFD99999')}")
    check("列超界 XFE1 拒绝", build_sheet_ref("t", "XFE1") == "",
          f"got={build_sheet_ref('t', 'XFE1')}")
    check("列超界 ZZZ1 拒绝（引擎可达但 Excel 不认）", build_sheet_ref("t", "ZZZ1") == "",
          f"got={build_sheet_ref('t', 'ZZZ1')}")
    check("行超界 A1048577 拒绝", build_sheet_ref("t", "A1048577") == "",
          f"got={build_sheet_ref('t', 'A1048577')}")
    check("区间第二端点超界 A1:XFE1 拒绝", build_sheet_ref("t", "A1:XFE1") == "",
          f"got={build_sheet_ref('t', 'A1:XFE1')}")
    check("_a1_in_bounds 满行 1048576 合法", cd._a1_in_bounds("XFD", "1048576") is True)
    check("_a1_in_bounds 超行 1048577 拒绝", cd._a1_in_bounds("A", "1048577") is False)
    check("_a1_in_bounds 满列 XFD 合法", cd._a1_in_bounds("XFD", "1") is True)
    check("_a1_in_bounds 超列 XFE 拒绝", cd._a1_in_bounds("XFE", "1") is False)

    # ===== 纯逻辑：list_sheet_names（排除当前表）=====
    orig = cd.cs.list_sheets
    try:
        cd.cs.list_sheets = lambda conn=None: [
            {"name": "主表"}, {"name": "2026-01"}, {"name": "辅助表"}]
        check("列出全部", list_sheet_names() == ["主表", "2026-01", "辅助表"],
              f"got={list_sheet_names()}")
        check("排除当前表", list_sheet_names(exclude="主表") == ["2026-01", "辅助表"],
              f"got={list_sheet_names(exclude='主表')}")
    finally:
        cd.cs.list_sheets = orig   # monkeypatch 必须恢复，避免污染同进程后续测试

    # ===== 离屏：SheetRefDialog =====
    app = QApplication.instance() or QApplication([])  # noqa: F841
    cd.cs.list_sheets = lambda conn=None: [{"name": "主表"}, {"name": "2026-01"}]
    try:
        dlg = SheetRefDialog(current_sheet="主表", default_a1="C3")
        check("对话框排除当前表", [dlg.f_sheet.itemText(i)
                                    for i in range(dlg.f_sheet.count())] == ["2026-01"],
              f"got={[dlg.f_sheet.itemText(i) for i in range(dlg.f_sheet.count())]}")
        check("默认地址取当前格", dlg.f_a1.text() == "C3", f"got={dlg.f_a1.text()}")
        check("预览自动加引号", dlg._current_formula() == "='2026-01'!C3",
              f"got={dlg._current_formula()}")
        dlg.f_abs_col.setChecked(True)
        check("勾选锁列后预览更新", dlg._current_formula() == "='2026-01'!$C3",
              f"got={dlg._current_formula()}")
        dlg.f_a1.setText("坏地址")
        check("非法地址预览为空", dlg._current_formula() == "",
              f"got={dlg._current_formula()}")
        dlg.f_a1.setText("A1")
        dlg._accept()
        check("确认后 formula 带引号", dlg.formula == "='2026-01'!$A1",
              f"got={dlg.formula}")
    finally:
        cd.cs.list_sheets = orig

    return 1 if FAILS else 0


if __name__ == "__main__":
    rc = main()
    print(f"PASS {OK}" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    sys.exit(rc)
