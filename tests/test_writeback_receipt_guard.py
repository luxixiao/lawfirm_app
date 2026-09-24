"""回写弹窗收款明细拦负数（P3-2 写入侧）单元测试 — offscreen，不写真实库。

collection 表不允许负数：红冲=负字发票、退款=独立退款台账，都不写 collection。
负数流入结算引擎会虚增其后未归因收款的分摊（QA 探针实测 2200 > 票面 1000）。
唯一可达入口 = 回写弹窗手输负数 → 此处硬拦。

用 object.__new__ 跳过 __init__（其会读真实库），只构造 self.tbl 测 _receipts 校验。
运行：python tests/test_writeback_receipt_guard.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QDialog, QTableWidget  # noqa: E402

from app.ui.writeback_dialog import WritebackDialog  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


class _ShellDlg(WritebackDialog):
    """只借 _receipts/_append_row 逻辑：跳过 __init__（其会读真实库）。"""

    def __init__(self):
        QDialog.__init__(self)  # 仅初始化 Qt 基类，不执行 WritebackDialog.__init__
        self.tbl = QTableWidget(0, 3)


def make_dlg(rows):
    """rows: [(ym, amount, person)]"""
    dlg = _ShellDlg()
    for ym, amt, person in rows:
        dlg._append_row(ym, amt, person)
    return dlg


def main() -> int:
    QApplication.instance() or QApplication([])

    # 1) 正数 + 千分位 + 空行跳过：正常收集
    dlg = make_dlg([("2025-03", "1,200.50", "张三"), ("", "", ""), ("2025-04", "300", "李四")])
    out = dlg._receipts()
    check("正数/千分位/空行跳过", out == [("张三", 1200.50, "2025-03"),
                                        ("李四", 300.0, "2025-04")], f"{out}")

    # 2) 负数 → ValueError（含「不允许为负数」与行号）
    dlg = make_dlg([("2025-03", "-500", "张三")])
    try:
        dlg._receipts()
        check("负数被拦截", False, "no exception")
    except ValueError as e:
        check("负数被拦截", "不允许为负数" in str(e) and "第 1 行" in str(e), str(e))

    # 3) 多行时定位到正确的行
    dlg = make_dlg([("2025-03", "100", "张三"), ("2025-03", "-1", "李四")])
    try:
        dlg._receipts()
        check("负数定位正确行", False, "no exception")
    except ValueError as e:
        check("负数定位正确行", "第 2 行" in str(e), str(e))

    # 4) 金额非数字仍走原有报错
    dlg = make_dlg([("2025-03", "abc", "张三")])
    try:
        dlg._receipts()
        check("非数字报错保留", False, "no exception")
    except ValueError as e:
        check("非数字报错保留", "无法解析" in str(e), str(e))

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
