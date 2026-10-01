"""花名册导入（无类型列）端到端单测（离屏 Qt，真实解析器）。

背景：花名册导出格式为 `编号|姓名|身份证号|手机号|入职月份|离职月份|备注`，
**不含「类型」列**。此前 `parse_staff_file` 硬要求表头含「类型」并套方案 B 整批拦截，
导致导出的花名册根本导不回（bug 报"未找到表头（需包含'姓名'和'类型'列）"）。

本文件守的口径：
- R1 纯花名册（无类型列）可直接导入，且 7 列字段全部落库（round-trip 无损）；
- R2 含「类型」列的文件（旧职工清单）仍兼容：类型照常建映射；
- N1 表头无「姓名」→ 抛 ImportError_ 并被 GUI 兜底提示，不写库。

运行：python tests/test_staff_roster_import.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="staff_roster_import_")) / "ui.db"

import app.db as appdb  # noqa: E402

appdb.DB_PATH = TMP

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

import app.ui.staff_view as sv  # noqa: E402

OK, FAILS = 0, []

_REAL_MSG = sv.QMessageBox
_REAL_FILE = sv.QFileDialog


class _MsgSpy:
    """替身弹窗：只记录调用，避免离屏下真的弹模态框。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def _rec(self, kind: str, title: str, text: str) -> None:
        self.calls.append((kind, title, text))

    def information(self, _p, title: str, text: str, *a, **k):
        self._rec("information", title, text)
        return QMessageBox.StandardButton.Ok

    def warning(self, _p, title: str, text: str, *a, **k):
        self._rec("warning", title, text)
        return QMessageBox.StandardButton.Ok

    def critical(self, _p, title: str, text: str, *a, **k):
        self._rec("critical", title, text)
        return QMessageBox.StandardButton.Cancel


class _FileSpy:
    """替身文件对话框：返回固定路径（真实 `parse_staff_file` 接管解析）。"""

    def __init__(self, path: str) -> None:
        self.path = path

    def getOpenFileName(self, *a, **k):
        return (self.path, "")


MSG = _MsgSpy()


def check(label: str, cond: bool, detail: str = "") -> None:
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def make_xlsx(path: str, headers, rows) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "花名册"
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


def snap_roster() -> dict:
    conn = sqlite3.connect(TMP)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT name, code, id_card, phone, hire_month, leave_month, note "
            "FROM staff_roster ORDER BY name")
        return {r["name"]: dict(r) for r in cur.fetchall()}
    finally:
        conn.close()


def do_import(xlsx_path: str):
    sv.QFileDialog = _FileSpy(xlsx_path)
    view = sv.StaffView()
    view.resize(900, 600)
    app.processEvents()
    view.import_staff()
    app.processEvents()
    view.close()
    return view


def main() -> int:
    try:
        sv.QMessageBox = MSG
        appdb.init_db(backfill=False)
        appdb.get_conn().close()

        # ===== R1 纯花名册（无类型列）7 列全部落库 =====
        p1 = str(TMP.parent / "roster1.xlsx")
        make_xlsx(p1,
                  ["编号", "姓名", "身份证号", "手机号", "入职月份", "离职月份", "备注"],
                  [["A001", "张三", "3301", "13800000000", "2020-01", "", "备注x"],
                   ["A002", "李四", "3302", "13900000000", "2021-03", "2022-05", "已离职"]])
        MSG.calls.clear()
        do_import(p1)
        check("R1：不弹「导入被拦下」（无类型列也能导）",
              not any(c[0] == "warning" and c[1] == "导入被拦下" for c in MSG.calls),
              f"got={MSG.calls}")
        check("R1：导入完成提示",
              ("information", "导入完成") in [(c[0], c[1]) for c in MSG.calls],
              f"got={MSG.calls}")
        rows = snap_roster()
        check("R1：两人入库", sorted(rows) == ["张三", "李四"], f"got={sorted(rows)}")
        z = rows["张三"]
        check("R1：编号落库", z["code"] == "A001", f"got={z}")
        check("R1：身份证落库", z["id_card"] == "3301", f"got={z}")
        check("R1：手机落库", z["phone"] == "13800000000", f"got={z}")
        check("R1：入职月份落库", z["hire_month"] == "2020-01", f"got={z}")
        check("R1：备注落库", z["note"] == "备注x", f"got={z}")
        l = rows["李四"]
        check("R1：离职月份落库", l["leave_month"] == "2022-05", f"got={l}")
        conn = appdb.get_conn()
        n_map = conn.execute("SELECT COUNT(*) FROM staff_type_map").fetchone()[0]
        conn.close()
        check("R1：无类型列 → 不产生类型映射", n_map == 0, f"got={n_map}")

        # ===== R2 含「类型」列（旧职工清单）仍兼容 =====
        p2 = str(TMP.parent / "roster2.xlsx")
        make_xlsx(p2,
                  ["编号", "姓名", "身份证号", "手机号", "入职月份", "离职月份", "备注", "类型"],
                  [["B001", "王五", "", "", "", "", "", "聘用"]])
        MSG.calls.clear()
        do_import(p2)
        rows = snap_roster()
        check("R2：含类型列的人入库", "王五" in rows, f"got={sorted(rows)}")
        conn = appdb.get_conn()
        mapped = conn.execute(
            "SELECT type_name FROM staff_type_map WHERE name='王五'").fetchall()
        types_def = conn.execute(
            "SELECT name FROM staff_type_def ORDER BY name").fetchall()
        conn.close()
        check("R2：类型照常建映射", [m[0] for m in mapped] == ["聘用"], f"got={mapped}")
        check("R2：类型按需补进类型表", [t[0] for t in types_def] == ["聘用"], f"got={types_def}")

        # ===== N1 表头无「姓名」→ 报错但不写库 =====
        p3 = str(TMP.parent / "bad.xlsx")
        make_xlsx(p3, ["foo", "bar"], [["x", "y"]])
        before = snap_roster()
        MSG.calls.clear()
        do_import(p3)
        kinds = [(c[0], c[1]) for c in MSG.calls]
        check("N1：无姓名表头 → 弹「导入失败」提示（不崩）",
              ("warning", "导入失败") in kinds, f"got={MSG.calls}")
        after = snap_roster()
        check("N1：无姓名表头 → 零写库", after == before, f"{before} → {after}")
        return 0
    finally:
        sv.QMessageBox = _REAL_MSG
        sv.QFileDialog = _REAL_FILE


if __name__ == "__main__":
    rc = main()
    print(f"PASS {OK} checks" if (rc == 0 and not FAILS) else
          ("FAILED:\n" + "\n".join(FAILS) if FAILS else "FAILED"))
    sys.exit(0 if (rc == 0 and not FAILS) else 1)
