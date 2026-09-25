"""手动添加职工：类型未选 / 类型表为空 → 不崩、不写库（连带伤修复）

背景（需求方 2026-09-25「员工类型去写死」）：员工类型表改为**初始为空**之后，
「手动添加职工」（`add_manual`）就成了一条必踩的崩溃路径 ——
`staff_view.py:474` `_type_combo()` 不带初值 → 类型表为空时下拉**没有选项** →
`currentData()` 返回 None → `:498` 直接 INSERT，而 `staff.staff_type` 是
**NOT NULL**（`app/db.py`），这段又只有 `try/finally` 没有 except →
`IntegrityError` 会崩出 Qt 槽。

修法按「不替用户决定」：`_type_combo()` 无初值时**不再预选第一项**（留空），
`add_manual` 在 **开连接之前**加守卫，两种情况给不同的提示：
- 类型表为空 → 「员工类型表还没有任何类型」+ 指向「员工类型」页；
- 有类型但没选 → 「请为该员工选择一个员工类型后再添加」。

覆盖：C1 类型表为空 / C2 有类型但没选 / C3 正常选中（回归保护）。

运行：python tests/test_staff_add_manual_type_guard.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="staff_add_manual_")) / "ui.db"

import app.db as appdb  # noqa: E402

appdb.DB_PATH = TMP

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.engine import staff_type as st  # noqa: E402
import app.ui.staff_view as sv  # noqa: E402

OK, FAILS = 0, []

_REAL_MSG = sv.QMessageBox
_REAL_DLG = sv.QDialog

# 每个用例要往对话框里填什么（由替身 `exec()` 在可接受前写入）
_FILL = {"name": "", "type": None}


class _FakeDialog(sv.QDialog):
    """替身对话框：不进模态循环，按 `_FILL` 把字段填好后直接返回 Accepted。

    填字段必须发生在 `exec()` 里 —— 那时 `add_manual` 已经把控件都加进 form 了，
    而控件本身是局部变量，外面拿不到。
    """

    def __init__(self, parent=None, *a, **k):
        super().__init__(parent, *a, **k)

    def exec(self):
        edits = self.findChildren(sv.QLineEdit)
        if edits and _FILL["name"]:
            edits[0].setText(_FILL["name"])          # form 里第一个是「姓名」
        combos = self.findChildren(sv.QComboBox)
        if combos and _FILL["type"] is not None:
            idx = combos[0].findText(_FILL["type"])
            if idx >= 0:
                combos[0].setCurrentIndex(idx)
        return sv.QDialog.DialogCode.Accepted


class _MsgSpy:
    """替身弹窗：只记录调用（含 add_manual 里可能弹的 information/warning）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def _rec(self, kind: str, title: str, text: str) -> None:
        self.calls.append((kind, title, text))

    def warning(self, parent, title: str, text: str, *a, **k):
        self._rec("warning", title, text)
        return QMessageBox.StandardButton.Ok

    def information(self, parent, title: str, text: str, *a, **k):
        self._rec("information", title, text)
        return QMessageBox.StandardButton.Ok

    def critical(self, parent, title: str, text: str, *a, **k):
        self._rec("critical", title, text)
        return QMessageBox.StandardButton.Ok


MSG = _MsgSpy()


def check(label: str, cond: bool, detail: str = "") -> None:
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def staff_rows():
    conn = sqlite3.connect(TMP)
    try:
        return conn.execute(
            "SELECT name, staff_type FROM staff ORDER BY name").fetchall()
    finally:
        conn.close()


def add_manual(name: str, type_name=None) -> None:
    """驱动一次「手动添加职工」。type_name=None 表示用户没动下拉。"""
    _FILL["name"] = name
    _FILL["type"] = type_name
    view = sv.StaffView()
    view.resize(900, 600)
    app.processEvents()
    try:
        view.add_manual()          # ← 过去在类型表为空时会崩 IntegrityError
    finally:
        view.close()
    app.processEvents()


def warnings_of(title: str):
    return [c for c in MSG.calls if c[0] == "warning" and c[1] == title]


def main() -> int:
    try:
        sv.QMessageBox = MSG  # type: ignore[assignment]
        sv.QDialog = _FakeDialog  # type: ignore[assignment]

        appdb.init_db(backfill=False)
        appdb.get_conn().close()
        check("前置：类型表初始为空", len(st.list_types()) == 0,
              f"got={[t['name'] for t in st.list_types()]}")

        # ===== C1）类型表为空 → 不崩、不写库、提示去建类型 =====
        MSG.calls.clear()
        before = staff_rows()
        add_manual("甲")
        check("C1：staff 表零写入", staff_rows() == before, f"got={staff_rows()}")
        w = warnings_of("未选择类型")
        check("C1：弹「未选择类型」", len(w) == 1, f"got={MSG.calls}")
        if w:
            check("C1：提示点明类型表还没有类型",
                  "员工类型" in w[0][2] and "新建" in w[0][2], f"got={w[0][2]!r}")

        # ===== C2）类型表有类型，但用户没选 → 不崩、不写库 =====
        st.add_type("合伙", conn=appdb.get_conn())
        check("C2：前置——类型表已有 1 个类型", len(st.list_types()) == 1,
              f"got={[t['name'] for t in st.list_types()]}")
        MSG.calls.clear()
        before2 = staff_rows()
        add_manual("乙")                      # 不动下拉 → 守卫应拦下
        check("C2：staff 表零写入（INSERT 前就 return）", staff_rows() == before2,
              f"got={staff_rows()}")
        w2 = warnings_of("未选择类型")
        check("C2：弹「未选择类型」", len(w2) == 1, f"got={MSG.calls}")
        if w2:
            check("C2：提示让用户自己选类型", "选择一个员工类型" in w2[0][2],
                  f"got={w2[0][2]!r}")

        # ===== C3）正常选中 → 能正常新增（回归保护）=====
        MSG.calls.clear()
        add_manual("丙", type_name="合伙")
        rows3 = staff_rows()
        check("C3：选中类型后成功新增", rows3 and rows3[0] == ("丙", "合伙"),
              f"got={rows3}")
        check("C3：不弹任何告警", not MSG.calls, f"got={MSG.calls}")
        return 0
    finally:
        sv.QMessageBox = _REAL_MSG    # type: ignore[assignment]
        sv.QDialog = _REAL_DLG        # type: ignore[assignment]


if __name__ == "__main__":
    rc = main()
    print(f"PASS {OK} checks" if (rc == 0 and not FAILS) else
          ("FAILED:\n" + "\n".join(FAILS) if FAILS else "FAILED"))
    sys.exit(0 if (rc == 0 and not FAILS) else 1)
