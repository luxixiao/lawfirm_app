"""calc_dialogs 无头冒烟 — 选择器/参数/指标管理（offscreen，不触真实库）。

运行：QT_QPA_PLATFORM=offscreen python tests/_smoke_calc_dialogs.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="calc_dlg_smoke_")) / "smoke.db"
import app.db as appdb  # noqa: E402
appdb.DB_PATH = TMP

from app.db import SCHEMA  # noqa: E402
from app.engine import calc_sheet as cs  # noqa: E402
from app.ui.calc_dialogs import (  # noqa: E402
    DataRefDialog, IndicatorManagerDialog, ParamDialog,
    build_data_formula, indicator_usage_count,
    validate_indicator_definition, validate_indicator_name, validate_param_name,
)

_conn = sqlite3.connect(TMP)
_conn.row_factory = sqlite3.Row
_conn.executescript(SCHEMA)
_conn.execute("INSERT INTO staff(name, staff_type, is_active) VALUES('周立生','聘用',1)")
_conn.execute("INSERT INTO staff(name, staff_type, is_active) VALUES('老李','合伙',0)")
_conn.execute("INSERT INTO import_batch(batch_type,period,file_name,imported_at) "
              "VALUES('ledger','2025-03','t.xls','2025-03-01')")
_conn.commit()
_conn.close()
conn2 = sqlite3.connect(TMP)
conn2.execute("INSERT INTO calc_indicator(name, definition, note) VALUES(?,?,?)",
              ("净分成", 'DATA($职工,"业务收入",$年,$月)*PARAM("k")', "测试"))
conn2.commit()
conn2.close()

# 一张引用了「净分成」的计算表（供 usage_count 扫描）
sid = cs.create_sheet("引用表")
c = cs.default_content(3, 2)
c["params"] = {"提成比例": 0.3}
c["cells"] = {"0,0": {"raw": '=DATA("周立生","净分成",2025,3)', "kind": "formula"}}
cs.save_content(sid, c)

import os  # noqa: E402
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QTableWidgetItem  # noqa: E402

app = QApplication.instance() or QApplication([])
OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


# ===== 纯逻辑 =====
check("公式 月度", build_data_formula("周立生", "业务收入", 2025, 3),
      '=DATA("周立生","业务收入",2025,3)')
check("公式 全年", build_data_formula("周立生", "业务收入", 2025, None),
      '=DATA("周立生","业务收入",2025)')
check("公式 名含引号转义", build_data_formula('周"立"', "指标", 2025, 1),
      '=DATA("周""立""","指标",2025,1)')
check("指标名 合法", validate_indicator_name("今年分成") is None)
check("指标名 禁函数重名", validate_indicator_name("SUM") is not None)
check("指标名 禁DATA", validate_indicator_name("DATA") is not None)
check("指标名 禁重复", validate_indicator_name("净分成") is not None)
check("指标名 禁空", validate_indicator_name("") is not None)
check("定义 合法", validate_indicator_definition('DATA($职工,"开票金额",$年)') is None)
check("定义 语法错", validate_indicator_definition('DATA($职工,"开票金额",$年') is not None)
check("定义 未知占位", validate_indicator_definition('DATA($部门,"开票金额",$年)') is not None)
check("定义 空拒绝", validate_indicator_definition("") is not None)
check("参数名 合法", validate_param_name("提成比例") is None)
check("参数名 禁空", validate_param_name("") is not None)
check("参数名 禁函数重名", validate_param_name("SUM") is not None)
check("参数名 禁引号", validate_param_name('a"b') is not None)
check("引用扫描 1 格", indicator_usage_count("净分成") == 1)
check("引用扫描 未用=0", indicator_usage_count("不存在的") == 0)

# ===== DataRefDialog =====
dlg = DataRefDialog()
idx_p = dlg.f_person.findData("周立生")
dlg.f_person.setCurrentIndex(idx_p)
idx_i = dlg.f_indicator.findData("业务收入")
dlg.f_indicator.setCurrentIndex(idx_i if idx_i >= 0 else 0)
check("年份下拉含 2025", dlg.f_year.findText("2025") >= 0,
      f"years={[dlg.f_year.itemText(i) for i in range(dlg.f_year.count())]}")
dlg.f_year.setCurrentText("2025")
dlg.f_month.setCurrentIndex(3)  # 3 月
check("选择器生成公式", dlg._current_formula(),
      '=DATA("周立生","业务收入",2025,3)')
check("离职人员带标记", "离职" in dlg.f_person.itemText(dlg.f_person.findData("老李")))
dlg.deleteLater()

# ===== ParamDialog =====
pd = ParamDialog({"提成比例": 0.3, "基准": 10000})
pd.table.setItem(1, 1, QTableWidgetItem("12000"))
pd._accept()
check("参数回读", pd.params == {"提成比例": 0.3, "基准": 12000.0},
      f"got={pd.params}")
pd.table.item(0, 1).setText("abc")
pd._accept()
check("参数非数值保留文本", pd.params.get("提成比例") == "abc", f"got={pd.params}")
pd.deleteLater()

# ===== IndicatorManagerDialog =====
im = IndicatorManagerDialog()
check("指标列表 1 行", im.table.rowCount() == 1, f"got={im.table.rowCount()}")
check("指标名显示", im.table.item(0, 0).text() == "净分成")
im.deleteLater()

print(f"SMOKE PASS {OK}" if not FAILS else "SMOKE FAILED:\n" + "\n".join(FAILS))
sys.exit(0 if not FAILS else 1)
