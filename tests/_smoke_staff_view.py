"""staff_view 无头冒烟 — Tab 化 + 修改/删除 + 员工类型维护（offscreen）。

运行：QT_QPA_PLATFORM=offscreen python tests/_smoke_staff_view.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="staff_smoke_")) / "smoke.db"
import app.db as appdb  # noqa: E402
appdb.DB_PATH = TMP

from app.db import SCHEMA  # noqa: E402
from app.engine import staff_type as st  # noqa: E402

conn = sqlite3.connect(TMP)
conn.row_factory = sqlite3.Row
conn.executescript(SCHEMA)
conn.execute("ALTER TABLE staff ADD COLUMN hire_month TEXT DEFAULT ''")
for n, t in (("周立生", "聘用"), ("王合伙", "合伙"), ("老李", "兼职"), ("待删员工", "其他")):
    conn.execute("INSERT INTO staff(name, staff_type, is_active) VALUES(?,?,1)", (n, t))
conn.commit()
conn.close()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

import app.ui.staff_view as sv  # noqa: E402

# 弹窗打桩：确认类一律 Yes，信息类一律关闭（避免 offscreen 阻塞）
sv.QMessageBox.question = staticmethod(
    lambda *a, **k: QMessageBox.StandardButton.Yes)
sv.QMessageBox.warning = staticmethod(lambda *a, **k: None)
sv.QMessageBox.information = staticmethod(lambda *a, **k: None)

view = sv.StaffView()
view.resize(1100, 700)
view.show()
app.processEvents()

fails = []
OK = 0


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        fails.append(f"{label} {detail}".strip())


# ===== Tab 结构 =====
check("2 个 Tab", view.tabs.count() == 2, f"got={view.tabs.count()}")
check("Tab1=员工名单", view.tabs.tabText(0) == "员工名单")
check("Tab2=员工类型", view.tabs.tabText(1) == "员工类型")

# ===== 员工名单 =====
check("4 名员工", view.table.rowCount() == 4, f"got={view.table.rowCount()}")
check("姓名列有值", view.table.item(0, 0).text() != "")

# ===== 员工类型 =====
check("预置 5 类", view.type_table.rowCount() == 5, f"got={view.type_table.rowCount()}")
first = view.type_table.item(0, 0).text()
check("首行=合伙", first == "合伙", f"got={first}")
check("合伙参与结算=是", view.type_table.item(0, 1).text() == "是")
check("其他不参与=—", view.type_table.item(4, 1).text() == "—",
      f"got={view.type_table.item(4, 1).text()}")
check("人数列=1(聘用)", view.type_table.item(1, 3).text() == "1",
      f"got={view.type_table.item(1, 3).text()}")

# ===== 新增类型 → 列表联动 =====
st.add_type("顾问", "外部顾问")
view.refresh()
app.processEvents()
check("新增后 6 类", view.type_table.rowCount() == 6, f"got={view.type_table.rowCount()}")

# ===== 员工编辑对话框的类型下拉读表 =====
combo = view._type_combo("聘用")
check("下拉含自定义类型", combo.findText("顾问") >= 0)
check("下拉当前值=聘用", combo.currentData() == "聘用")

# ===== 删除无引用员工（完整按钮路径，含确认弹窗）=====
n0 = view.table.rowCount()
row = [r for r in range(view.table.rowCount())
       if view.table.item(r, 0).text() == "待删员工"][0]
view.table.selectRow(row)
view.delete_selected()
app.processEvents()
check("删除后减少 1 人", view.table.rowCount() == n0 - 1,
      f"{n0} -> {view.table.rowCount()}")

# ===== 有引用的员工：按钮路径应被拒绝（弹 warning 已打桩为 no-op）=====
c2 = sqlite3.connect(TMP)
c2.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount) "
           "VALUES('INV1','2025-03-01',1000)")
c2.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount) "
           "VALUES('INV1','周立生',1000)")
c2.commit()
c2.close()
view.refresh()
app.processEvents()
n1 = view.table.rowCount()
row2 = [r for r in range(view.table.rowCount())
        if view.table.item(r, 0).text() == "周立生"][0]
view.table.selectRow(row2)
view.delete_selected()
app.processEvents()
check("有引用员工未被删除", view.table.rowCount() == n1,
      f"{n1} -> {view.table.rowCount()}")

# ===== 像素冒烟 =====
img = view.grab()
qi = img.toImage()
nonwhite = sum(1 for x in range(0, qi.width(), 40)
               for y in range(0, qi.height(), 40)
               if qi.pixelColor(x, y).red() < 240)
check("像素非全白", nonwhite > 5, f"nonwhite={nonwhite}")
img.save(str(ROOT / "tests" / "_smoke_staff_view.png"))

print(f"SMOKE PASS {OK}" if not fails else "SMOKE FAILED:\n" + "\n".join(fails))
sys.exit(0 if not fails else 1)
