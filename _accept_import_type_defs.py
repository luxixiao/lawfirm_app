"""类型设置导入 —— 无头(offscreen) GUI 验收脚本（用户本机 venv 运行）。

直接驱动真实的 `StaffView.import_type_defs` UI 方法（不是只测引擎）：
- 文件选择框 / 提示框 被替换为「捕获」，不弹真对话框；
- `get_conn` 指向**临时文件库**（预置用户真实场景：已含 7 个标准类型），不碰真库；
- 跑完断言：对话框显示「新增 4 / 跳过已存在 7」，且 实习/家属/单独项目/6666 入库、7 原类型保留。

运行（本机 venv，项目根目录）：
    cd 测试/lawfirm_app
    QT_QPA_PLATFORM=offscreen python _accept_import_type_defs.py
退出码 0 = 验收通过；非 0 = 失败（脚本会打印明细）。
"""
import os
import sys
import sqlite3
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # 无显示也能跑 Qt

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication(sys.argv)  # offscreen 下只需一个实例

from app.db import SCHEMA  # noqa: E402
from app.engine import staff_type as st  # noqa: E402
import app.ui.staff_view as sv  # noqa: E402

# ---- 1) 准备临时库，模拟用户「已含 7 个标准类型」的真实场景 ----
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
DBPATH = tmp.name
tmp.close()
conn = sqlite3.connect(DBPATH)
conn.executescript(SCHEMA)
for col, ddl in (("is_invoice", "INTEGER NOT NULL DEFAULT 0"),
                 ("can_expense", "INTEGER NOT NULL DEFAULT 0"),
                 ("net_basis", "TEXT NOT NULL DEFAULT '收款净额'")):
    if col not in [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]:
        conn.execute(f"ALTER TABLE staff_type_def ADD COLUMN {col} {ddl}")
conn.commit()
for nm in ("合伙", "聘用", "兼职", "行政", "公共", "挂靠", "其他"):
    st.add_type(nm, conn=conn)
    conn.commit()
conn.close()

# ---- 2) 把 UI 方法接到临时库（不碰真库）----
sv.get_conn = lambda: sqlite3.connect(DBPATH)

# ---- 3) 捕获对话框（不弹窗）----
captured = []
sv.QMessageBox.information = lambda title, text, *a, **k: captured.append(("information", title, text))
sv.QMessageBox.warning = lambda title, text, *a, **k: captured.append(("warning", title, text))
sv.QMessageBox.critical = lambda title, text, *a, **k: captured.append(("critical", title, text))

# ---- 4) 文件选择框直接返回 类型设置.xlsx ----
FILE = ROOT / "类型设置.xlsx"
if not FILE.exists():
    print("SKIP: 项目根未找到 类型设置.xlsx")
    sys.exit(2)
sv.QFileDialog.getOpenFileName = lambda *a, **k: (str(FILE), "")

# ---- 5) 用 fake self 调真实 UI 方法（仅需 _refresh_types）----
class _FakeSelf:
    def _refresh_types(self):
        pass

try:
    sv.StaffView.import_type_defs(_FakeSelf())
except Exception as e:  # noqa: BLE001
    import traceback
    print("EXCEPTION:", repr(e))
    traceback.print_exc()
    sys.exit(1)

# ---- 6) 验收断言 ----
print("捕获到的对话框：")
for c in captured:
    print("   ", c[0], "|", c[1], "|", c[2])

ok_dlg = any(c[0] == "information" and "新增 4" in c[2] and "跳过已存在 7" in c[2]
             for c in captured)

c = sqlite3.connect(DBPATH)
c.row_factory = sqlite3.Row
names = [r["name"] for r in c.execute("SELECT name FROM staff_type_def ORDER BY name")]
expect_new = {"实习", "家属", "单独项目", "6666"}
missing = [n for n in expect_new if n not in names]
keep_seven = all(n in names for n in ("合伙", "聘用", "兼职", "行政", "公共", "挂靠", "其他"))
c.close()
try:
    os.remove(DBPATH)
except OSError:
    pass

print("\n库内类型数:", len(names))
print("库内:", names)
print("\n=== 验收结论 ===")
print("对话框显示「新增 4 / 跳过已存在 7」:", ok_dlg)
print("4 个新类型已入库:", not missing, "(缺失:" + ",".join(missing) + ")")
print("7 个原类型保留:", keep_seven)
passed = ok_dlg and not missing and keep_seven
print("PASS" if passed else "FAIL")
sys.exit(0 if passed else 1)
