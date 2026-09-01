"""calc_sheet_view 无头 UI 冒烟（offscreen）— 验证 网格填充/求值显示/编辑回写。

运行（须有 PySide6，无需 qfluentwidgets）：
    QT_QPA_PLATFORM=offscreen python tests/_smoke_calc_view.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402

# ---- 先把 DB 指到临时库，再导入引擎 ----
TMP = Path(tempfile.mkdtemp(prefix="calc_smoke_")) / "smoke.db"
import app.db as appdb  # noqa: E402
appdb.DB_PATH = TMP

from app.db import SCHEMA  # noqa: E402
from app.engine import calc_sheet as cs  # noqa: E402

_conn = sqlite3.connect(TMP)
_conn.row_factory = sqlite3.Row
_conn.executescript(SCHEMA)
_conn.commit()
_conn.close()

# ---- 种子：一张计算表（参数 + 公式 + 跨格引用）----
sid = cs.create_sheet("冒烟表", updated_by="smoke")
content = cs.default_content(5, 3)
content["params"] = {"k": 0.3}
content["cells"] = {
    "0,0": {"raw": "基数", "kind": "text"},
    "0,1": {"raw": "100", "kind": "number"},
    "1,0": {"raw": '=B1*PARAM("k")', "kind": "formula"},
}
cs.save_content(sid, content, updated_by="smoke")

# ---- offscreen 构造 UI ----
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from app.ui.calc_sheet_view import CalcSheetView  # noqa: E402

view = CalcSheetView()
view.resize(1000, 600)
view.show()
app.processEvents()

fails = []


def check(label, cond, detail=""):
    if not cond:
        fails.append(f"{label} {detail}".strip())


# 列表自动选中 → 网格填充
check("列表 1 项", view.list.count() == 1, f"got={view.list.count()}")
check("已选 sheet", view.sheet_id == sid, f"got={view.sheet_id}")
it = view.table.item(1, 0)
check("公式格显示计算值 30", it is not None and it.text() == "30",
      f"got={it.text() if it else None}")
check("数字格显示 100", view.table.item(0, 1).text() == "100")
check("文本格显示 基数", view.table.item(0, 0).text() == "基数")

# 选中格 → 公式栏显示原文
view.table.setCurrentCell(1, 0)
app.processEvents()
check("公式栏显示 raw", view.fx.text() == '=B1*PARAM("k")', f"got={view.fx.text()!r}")
check("坐标标签 A2", view.lbl_cell.text() == "A2", f"got={view.lbl_cell.text()}")

# 查看模式默认只读
check("默认查看模式", not view.edit_mode)
check("查看模式公式栏只读", view.fx.isReadOnly())

# 编辑：写入公式 → 自动保存 → 重算显示
view._set_mode(True)
check("编辑模式公式栏可写", not view.fx.isReadOnly())
view._write_cell(2, 0, "=B1*2")
app.processEvents()
it2 = view.table.item(2, 0)
check("新公式显示 200", it2 is not None and it2.text() == "200",
      f"got={it2.text() if it2 else None}")
# 已落库
back = cs.get_sheet(sid)
check("编辑已存库", back["content"]["cells"]["2,0"]["raw"] == "=B1*2")
check("updated_by 已刷新", back["updated_by"] != "", f"got={back['updated_by']!r}")

# 改参数值 → 结果联动
c2 = back["content"]
c2["params"]["k"] = 0.5
cs.save_content(sid, c2, updated_by="smoke")
view._load_sheet()
app.processEvents()
check("参数 0.5 后公式=50", view.table.item(1, 0).text() == "50",
      f"got={view.table.item(1, 0).text()}")

# 错误值红色显示
view._write_cell(3, 0, "=1/0")
app.processEvents()
it3 = view.table.item(3, 0)
check("除零显示 #DIV/0!", it3 is not None and it3.text() == "#DIV/0!",
      f"got={it3.text() if it3 else None}")

# TSV 值粘贴（2×2 区域）
from PySide6.QtWidgets import QApplication as _Q  # noqa: E402
_Q.clipboard().setText("10\t20\n30\t40")
view.table.setCurrentCell(4, 0)
view.paste_tsv()
app.processEvents()
check("粘贴 20→(4,1)", view.table.item(4, 1).text() == "20",
      f"got={view.table.item(4, 1).text() if view.table.item(4, 1) else None}")
check("粘贴自动加行 40→(5,1)", view.table.item(5, 1).text() == "40",
      f"got={view.table.item(5, 1).text() if view.table.item(5, 1) else None}")
check("粘贴数字右对齐值", view.table.item(4, 0).text() == "10")

# ---- 新功能回归：冻结首行 / 缩放 / 折叠 / 选中统计 ----
# 冻结首行默认开：副表仅 1 行，且镜像主表第 1 行内容
check("冻结首行默认可见", view.frozen.isVisible(),
      f"visible={view.frozen.isVisible()}")
check("冻结副表仅 1 行", view.frozen.rowCount() == 1,
      f"rows={view.frozen.rowCount()}")
check("冻结副表镜像首行 B1=100",
      view.frozen.item(0, 1) is not None and view.frozen.item(0, 1).text() == "100",
      f"got={view.frozen.item(0,1).text() if view.frozen.item(0,1) else None}")
# Ctrl+滚轮放大：zoom 增长、标签更新
before_z = view._zoom
view._on_zoom(1)
check("Ctrl+滚轮放大 zoom 增长", view._zoom > before_z,
      f"before={before_z} after={view._zoom}")
check("缩放标签已更新", view.lbl_zoom.text().endswith("%"),
      f"got={view.lbl_zoom.text()!r}")
# 复位
view._reset_zoom()
check("缩放复位 100%", view._zoom == 1.0 and view.lbl_zoom.text() == "100%",
      f"got={view.lbl_zoom.text()!r}")
# 左栏折叠：offscreen 下 isVisible 初始不可靠（窗口未真正映射），
# 先强制可见，再 toggle 验证变隐藏、再复原（真实窗口里 isVisible 初始即 True，行为一致）
view.left_widget.setVisible(True)
app.processEvents()
view._toggle_list()
app.processEvents()
check("左栏可折叠", view.left_widget.isHidden(),
      f"hidden={view.left_widget.isHidden()}")
view.left_widget.setVisible(True)
app.processEvents()  # 复位，避免影响后续像素冒烟
# 选中区域统计：选中 A1:A3（基数/公式/公式）后求和应含数值项
from PySide6.QtWidgets import QTableWidgetSelectionRange
view.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 2, 0), True)
view._update_stats()
stat = view.lbl_stat.text()
check("选中统计非空", stat != "",
      f"got={stat!r}")
check("选中统计含 计数", "计数" in stat, f"stat={stat!r}")
check("选中统计含 求和", "求和" in stat, f"stat={stat!r}")

# 像素冒烟：grab 非空白
img = view.grab()
qi = img.toImage()
nonwhite = 0
for x in range(0, qi.width(), 40):
    for y in range(0, qi.height(), 40):
        c = qi.pixelColor(x, y)
        if c.red() < 240 or c.green() < 240 or c.blue() < 240:
            nonwhite += 1
check("像素非全白", nonwhite > 5, f"nonwhite={nonwhite}")
out = ROOT / "tests" / "_smoke_calc_view.png"
img.save(str(out))

print("SMOKE PASS" if not fails else "SMOKE FAILED:\n" + "\n".join(fails))
sys.exit(0 if not fails else 1)
