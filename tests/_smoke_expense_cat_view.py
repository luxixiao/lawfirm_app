"""expense_cat_view 无头冒烟 — 分类卡片 + 类内/跨类拖拽 + 增改删（offscreen）。

运行：QT_QPA_PLATFORM=offscreen python tests/_smoke_expense_cat_view.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="expcat_smoke_")) / "smoke.db"
import app.db as appdb  # noqa: E402
appdb.DB_PATH = TMP

from app.db import SCHEMA  # noqa: E402
from app.engine import expense_cat as ec  # noqa: E402

conn = sqlite3.connect(TMP)
conn.row_factory = sqlite3.Row
conn.executescript(SCHEMA)
for t, c in (("分成（报酬发放）", "报酬发放"), ("工资", "报酬发放"),
             ("公积金", "住房公积金"), ("社保", "保险费"), ("商业保险", "保险费"),
             ("汽油费", "汽油费"), ("停车费", "汽油费"), ("办公用品", "其他")):
    conn.execute("INSERT INTO expense_cat(expense_type, category) VALUES(?,?)", (t, c))
conn.commit()
conn.close()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

import app.ui.expense_cat_view as ecv  # noqa: E402

# 弹窗打桩：确认类一律 Yes，信息/警告一律关闭；输入类返回固定文本
ecv.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
ecv.QMessageBox.warning = staticmethod(lambda *a, **k: None)
ecv.QMessageBox.information = staticmethod(lambda *a, **k: None)
ecv.QInputDialog.getText = staticmethod(lambda *a, **k: (_ANSWER, True))

_ANSWER = ""


class _FakeNoteDialog:
    def __init__(self, *a, **k) -> None:
        pass

    def exec(self):
        return QDialog.DialogCode.Accepted

    def value(self) -> str:
        return "车辆相关支出：加油 / 过路 / 停车"


ecv.NoteDialog = _FakeNoteDialog

view = ecv.ExpenseCatView()
view.resize(1180, 760)
view.show()
app.processEvents()

fails, OK = [], 0


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        fails.append(f"{label} {detail}".strip())


def card(name):
    return view._cards[name]


# ===== 1. 卡片结构 =====
check("5 张分类卡片", len(view._cards) == 5, f"got={len(view._cards)}")
check("分类齐全", list(view._cards) == ec.CATEGORIES, f"got={list(view._cards)}")
check("报酬发放 2 项", card("报酬发放").list.count() == 2)
check("住房公积金 1 项", card("住房公积金").list.count() == 1)
check("保险费 2 项", card("保险费").list.count() == 2)
check("汽油费 2 项", card("汽油费").list.count() == 2)
check("其他 1 项", card("其他").list.count() == 1)
check("计数徽标同步", card("保险费").count.text() == "2 项",
      f"got={card('保险费').count.text()}")

# ===== 2. 类内拖动重排（drop 逻辑） =====
# 初始未维护顺序 → 按类型名兜底：保险费=商业保险/社保，汽油费=停车费/汽油费
gas = card("汽油费").list
check("初始兜底序", gas.type_names() == ["停车费", "汽油费"], f"got={gas.type_names()}")

# 把「停车费」拖到末尾
gas.apply_drop(["停车费"], gas, 2)
app.processEvents()
check("类内重排 UI", gas.type_names() == ["汽油费", "停车费"], f"got={gas.type_names()}")
check("类内重排落库", ec.types_by_category()["汽油费"] == ["汽油费", "停车费"],
      f"got={ec.types_by_category()['汽油费']}")

# 再拖回首位
gas.apply_drop(["停车费"], gas, 0)
app.processEvents()
check("拖回首位 UI", gas.type_names() == ["停车费", "汽油费"], f"got={gas.type_names()}")
check("拖回首位落库", ec.types_by_category()["汽油费"] == ["停车费", "汽油费"],
      f"got={ec.types_by_category()['汽油费']}")

# ===== 3. 跨卡片拖动（改归类） =====
# 把「办公用品」从「其他」拖到「保险费」首位
ins = card("保险费").list
other = card("其他").list
ins.apply_drop(["办公用品"], other, 0)
app.processEvents()
check("跨类搬走后源卡清空", other.type_names() == [], f"got={other.type_names()}")
check("跨类搬运后目标卡", ins.type_names() == ["办公用品", "商业保险", "社保"],
      f"got={ins.type_names()}")
check("跨类搬运落库归类", ec.get_map()["办公用品"] == "保险费")
check("跨类搬运落库顺序", ec.get_by_category("保险费") == ["办公用品", "商业保险", "社保"],
      f"got={ec.get_by_category('保险费')}")
check("计数徽标已刷新", card("其他").count.text() == "0 项",
      f"got={card('其他').count.text()}")

# ===== 4. 全局顺序 = 分类顺序 + 类内顺序 =====
want = ["分成（报酬发放）", "工资", "公积金", "办公用品", "商业保险", "社保",
        "停车费", "汽油费"]
check("ordered_types 全局序", ec.ordered_types() == want, f"got={ec.ordered_types()}")

# ===== 5. 上移 / 下移按钮 =====
ins.item(1).setSelected(True)          # 商业保险
view.move_in_category("商业保险", -1)
app.processEvents()
check("上移生效", ec.get_by_category("保险费") == ["商业保险", "办公用品", "社保"],
      f"got={ec.get_by_category('保险费')}")
view.move_in_category("商业保险", 1)
app.processEvents()
check("下移生效", ec.get_by_category("保险费") == ["办公用品", "商业保险", "社保"],
      f"got={ec.get_by_category('保险费')}")

# ===== 6. 新增类型 =====
_ANSWER = "差旅费"
card("其他")._add()
app.processEvents()
check("新增落库", "差旅费" in ec.get_map(), f"got={list(ec.get_map())}")
check("新增归到目标类", ec.get_by_category("其他") == ["差旅费"],
      f"got={ec.get_by_category('其他')}")
_ANSWER = "差旅费"          # 重名 → warning 打桩为 no-op，不应重复入库
card("其他")._add()
app.processEvents()
check("重名不重复入库", ec.get_by_category("其他") == ["差旅费"],
      f"got={ec.get_by_category('其他')}")

# ===== 7. 改名（双击路径） =====
card("其他").list.itemDoubleClicked.emit(card("其他").list.item(0))
app.processEvents()          # 上一次 _ANSWER 还是"差旅费" → new==old，不改
_ANSWER = "差旅住宿费"
card("其他").list.itemDoubleClicked.emit(card("其他").list.item(0))
app.processEvents()
check("改名落库", ec.get_by_category("其他") == ["差旅住宿费"],
      f"got={ec.get_by_category('其他')}")

# ===== 8. 分类说明 =====
card("汽油费")._edit_note()
app.processEvents()
check("说明写入库", ec.get_category_note("汽油费") == "车辆相关支出：加油 / 过路 / 停车")
check("说明显示在卡片", "车辆相关" in card("汽油费").note_label.text(),
      f"got={card('汽油费').note_label.text()}")
check("其它类说明仍为空", ec.get_category_note("保险费") == "")

# ===== 9. 删除类型 =====
card("保险费").list.item(0).setSelected(True)     # 办公用品
view.delete_types(["办公用品"], "保险费")
app.processEvents()
check("删除落库", "办公用品" not in ec.get_map(), f"got={list(ec.get_map())}")
check("删除后保险费 2 项", ec.get_by_category("保险费") == ["商业保险", "社保"],
      f"got={ec.get_by_category('保险费')}")

# ===== 10. 移动到其它分类（菜单路径） =====
view.move_to_category(["差旅住宿费"], "报酬发放")
app.processEvents()
check("移动到目标类", ec.get_by_category("报酬发放")[-1] == "差旅住宿费",
      f"got={ec.get_by_category('报酬发放')}")
check("源类已空", ec.get_by_category("其他") == [], f"got={ec.get_by_category('其他')}")

# ===== 11. 刷新后状态保持 =====
view.refresh()
app.processEvents()
check("刷新后卡片数不变", len(view._cards) == 5)
check("刷新后报酬发放 3 项", card("报酬发放").list.count() == 3,
      f"got={card('报酬发放').list.count()}")
check("底部统计", view.hint.text().startswith("共 8 个费用类型"), f"got={view.hint.text()}")

# ===== 12. 像素冒烟 =====
img = view.grab()
qi = img.toImage()
nonwhite = sum(1 for x in range(0, qi.width(), 40)
               for y in range(0, qi.height(), 40)
               if qi.pixelColor(x, y).red() < 240)
check("像素非全白", nonwhite > 5, f"nonwhite={nonwhite}")
img.save(str(ROOT / "tests" / "_smoke_expense_cat_view.png"))

print(f"SMOKE PASS {OK}" if not fails else "SMOKE FAILED:\n" + "\n".join(fails))
sys.exit(0 if not fails else 1)
