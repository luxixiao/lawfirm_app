"""导入页（`import_view`）花名册导入端到端单测（离屏 Qt，真实解析器）。

覆盖：
- R   纯花名册（无类型列）成功导入且 7 列全部落库；
- R2  含「类型」列（旧职工清单）仍兼容，类型照常建映射；
- 全程不弹「导入被拦下」、不抛 MissingStaffTypeError（方案 B 已随花名册格式调整移除）。

运行：python tests/test_import_view_roster_import.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="import_view_roster_")) / "ui.db"

import app.db as appdb  # noqa: E402

appdb.DB_PATH = TMP

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

import app.ui.import_view as IV  # noqa: E402

OK, FAILS = 0, []

_REAL_MSG = IV.QMessageBox


class _MsgSpy:
    """替身弹窗：只记录，避免离屏下真弹模态框。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def _rec(self, kind: str, title: str, text: str) -> None:
        self.calls.append((kind, title, text))

    def warning(self, _p, title: str, text: str, *a, **k):
        self._rec("warning", title, text)
        return QMessageBox.StandardButton.Ok

    def information(self, _p, title: str, text: str, *a, **k):
        self._rec("information", title, text)
        return QMessageBox.StandardButton.Ok

    def critical(self, _p, title: str, text: str, *a, **k):
        self._rec("critical", title, text)
        return QMessageBox.StandardButton.Ok

    def question(self, _p, title: str, text: str, *a, **k):
        self._rec("question", title, text)
        return QMessageBox.StandardButton.No


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


def snap() -> tuple:
    conn = sqlite3.connect(TMP)
    try:
        conn.row_factory = sqlite3.Row
        n = conn.execute("SELECT COUNT(*) FROM staff_roster").fetchone()[0]
        rows = {r["name"]: dict(r) for r in conn.execute(
            "SELECT name, code, id_card, phone, hire_month, leave_month, note "
            "FROM staff_roster").fetchall()}
        types = [t[0] for t in conn.execute(
            "SELECT name FROM staff_type_def ORDER BY name").fetchall()]
        mapped = conn.execute(
            "SELECT name, type_name FROM staff_type_map ORDER BY name").fetchall()
        return n, rows, types, mapped
    finally:
        conn.close()


def do_import(xlsx_path: str, quiet: bool = False):
    view = IV.ImportView()
    view.resize(900, 600)
    app.processEvents()
    view._do_import(xlsx_path, quiet=quiet)
    app.processEvents()
    view.close()
    return view


def main() -> int:
    try:
        IV.QMessageBox = MSG
        appdb.init_db(backfill=False)
        appdb.get_conn().close()

        # ===== R 纯花名册（无类型列）7 列落库，不拦截 =====
        # 文件名含「花名册」→ guess_type 判为 staff
        p = str(TMP.parent / "花名册测试.xlsx")
        make_xlsx(p,
                  ["编号", "姓名", "身份证号", "手机号", "入职月份", "离职月份", "备注"],
                  [["A001", "张三", "3301", "13800000000", "2020-01", "", "备注x"]])
        MSG.calls.clear()
        do_import(p)
        n, rows, types, mapped = snap()
        check("R：不弹「导入被拦下」",
              not any(c[0] == "warning" and c[1] == "导入被拦下" for c in MSG.calls),
              f"got={MSG.calls}")
        check("R：入库 1 人", n == 1, f"got={n}")
        check("R：姓名入库", "张三" in rows, f"got={sorted(rows)}")
        z = rows["张三"]
        check("R：编号落库", z["code"] == "A001", f"got={z}")
        check("R：身份证落库", z["id_card"] == "3301", f"got={z}")
        check("R：手机落库", z["phone"] == "13800000000", f"got={z}")
        check("R：入职月份落库", z["hire_month"] == "2020-01", f"got={z}")
        check("R：备注落库", z["note"] == "备注x", f"got={z}")
        check("R：无类型列 → 无类型映射", mapped == [], f"got={mapped}")

        # ===== R2 含「类型」列（旧职工清单）仍建映射 =====
        p2 = str(TMP.parent / "职工清单测试.xlsx")
        make_xlsx(p2,
                  ["编号", "姓名", "身份证号", "手机号", "入职月份", "离职月份", "备注", "类型"],
                  [["B001", "王五", "", "", "", "", "", "合伙"]])
        MSG.calls.clear()
        do_import(p2)
        n2, rows2, types2, mapped2 = snap()
        check("R2：含类型列的人入库", "王五" in rows2, f"got={sorted(rows2)}")
        check("R2：类型建映射",
              [m[1] for m in mapped2 if m[0] == "王五"] == ["合伙"], f"got={mapped2}")
        check("R2：类型补进类型表", types2 == ["合伙"], f"got={types2}")
        return 0
    finally:
        IV.QMessageBox = _REAL_MSG


if __name__ == "__main__":
    rc = main()
    print(f"PASS {OK} checks" if (rc == 0 and not FAILS) else
          ("FAILED:\n" + "\n".join(FAILS) if FAILS else "FAILED"))
    sys.exit(0 if (rc == 0 and not FAILS) else 1)
