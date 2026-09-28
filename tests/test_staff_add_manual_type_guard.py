"""手动添加职工（新架构：仅填人员 → 写 staff_roster；类型在 Tab2 单独分配）

背景（需求方 2026-09-25「员工类型去写死」）：员工类型表初始为空后，「手动添加职工」
的对话框（`add_roster` → `_roster_dialog`）只填人员字段（姓名/编号/身份证/...），
类型在「员工类型」页（人×类型网格）经引擎 API 单独关联，不再在添加弹窗里强制选类型。

本测试覆盖新架构下的手动添加回归：
- C1 类型表为空时手动添加填姓名 → 成功写入 staff_roster（不崩、确有该行、暂无类型）；
- C2 姓名主键唯一 → 重复添加被拦（弹「新增失败」、不写第二条）；
- C3 新增后经 `set_person_types` 关联类型 → staff_type_map 出现该人主类型（新路径回归）。

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
_FILL = {"name": ""}


class _FakeDialog(sv.QDialog):
    """替身对话框：不进模态循环，按 `_FILL` 把「姓名」字段填好后直接返回 Accepted。

    填字段必须发生在 `exec()` 里 —— 那时 `add_roster` 已经把控件都加进 form 了，
    而控件本身是局部变量，外面拿不到。`_roster_dialog` 的字段顺序为
    编号 / 姓名 / 身份证 / 手机 / 入职 / 离职 / 备注，姓名是第 2 个 QLineEdit。
    """

    def __init__(self, parent=None, *a, **k):
        super().__init__(parent, *a, **k)

    def exec(self):
        edits = self.findChildren(sv.QLineEdit)
        if len(edits) >= 2 and _FILL["name"]:
            edits[1].setText(_FILL["name"])          # 第 2 个 QLineEdit = 姓名
        return sv.QDialog.DialogCode.Accepted


class _MsgSpy:
    """替身弹窗：只记录调用（含新增失败等 critical/warning）。"""

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


def roster_names():
    conn = sqlite3.connect(TMP)
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM staff_roster ORDER BY name").fetchall()]
    finally:
        conn.close()


def staff_rows():
    """带主类型的 (姓名, 类型) 列表（新架构下类型在 staff_type_map 关联）。"""
    conn = sqlite3.connect(TMP)
    try:
        return conn.execute(
            "SELECT r.name, m.type_name AS staff_type FROM staff_roster r "
            "JOIN staff_type_map m ON m.name=r.name AND m.is_primary=1 "
            "ORDER BY r.name").fetchall()
    finally:
        conn.close()


def add_one(name: str) -> None:
    """驱动一次「手动添加职工」（新架构 = add_roster 仅填人员）。"""
    _FILL["name"] = name
    view = sv.StaffView()
    view.resize(900, 600)
    app.processEvents()
    try:
        view.add_roster()          # 开连接、弹 _roster_dialog（替身直接 Accepted）
    finally:
        view.close()
    app.processEvents()


def criticals_of(title: str):
    return [c for c in MSG.calls if c[0] == "critical" and c[1] == title]


def main() -> int:
    try:
        sv.QMessageBox = MSG  # type: ignore[assignment]
        sv.QDialog = _FakeDialog  # type: ignore[assignment]

        appdb.init_db(backfill=False)
        appdb.get_conn().close()
        check("前置：类型表初始为空", len(st.list_types()) == 0,
              f"got={[t['name'] for t in st.list_types()]}")

        # ===== C1）类型表为空时手动添加填姓名 → 成功写入 staff_roster（不崩、暂无类型）=====
        MSG.calls.clear()
        add_one("甲")
        check("C1：写入 staff_roster", roster_names() == ["甲"], f"got={roster_names()}")
        check("C1：暂无类型（类型表为空，不强制选）", staff_rows() == [], f"got={staff_rows()}")
        check("C1：不弹任何告警", not MSG.calls, f"got={MSG.calls}")

        # ===== C2）姓名主键唯一 → 重复添加被拦（弹「新增失败」、不写第二条）=====
        MSG.calls.clear()
        add_one("甲")                      # 同名 → 主键冲突
        check("C2：仍是 1 行（未写第二条）", roster_names() == ["甲"], f"got={roster_names()}")
        c = criticals_of("新增失败")
        check("C2：弹「新增失败」", len(c) == 1, f"got={MSG.calls}")

        # ===== C3）新增后经引擎 API 关联类型（Tab2 新路径回归）=====
        st.add_type("合伙", conn=appdb.get_conn())
        st.set_person_types("甲", ["合伙"], conn=appdb.get_conn())
        rows3 = staff_rows()
        check("C3：关联类型后 staff_rows 出现 (甲, 合伙)", rows3 == [("甲", "合伙")],
              f"got={rows3}")
        check("C3：主类型 = 合伙", st.primary_type_of("甲") == "合伙",
              f"got={st.primary_type_of('甲')}")
        return 0
    finally:
        sv.QMessageBox = _REAL_MSG    # type: ignore[assignment]
        sv.QDialog = _REAL_DLG        # type: ignore[assignment]


if __name__ == "__main__":
    rc = main()
    print(f"PASS {OK} checks" if (rc == 0 and not FAILS) else
          ("FAILED:\n" + "\n".join(FAILS) if FAILS else "FAILED"))
    sys.exit(0 if (rc == 0 and not FAILS) else 1)
