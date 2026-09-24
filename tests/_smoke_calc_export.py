"""calc_export 冒烟 — 导出规则（带公式/不带公式），openpyxl 回读校验。

运行：python tests/_smoke_calc_export.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="calc_exp_smoke_")) / "smoke.db"
import app.db as appdb  # noqa: E402
appdb.DB_PATH = TMP

from app.db import SCHEMA  # noqa: E402
from app.engine import calc_sheet as cs  # noqa: E402
from tests.test_calc_data import seed  # noqa: E402   复用业务种子数据

conn = sqlite3.connect(TMP)
conn.row_factory = sqlite3.Row
conn.executescript(SCHEMA)
for tbl in ("charge_detail", "expense_ledger"):
    conn.execute(f"ALTER TABLE {tbl} ADD COLUMN person_type TEXT DEFAULT ''")
seed(conn)
conn.commit()

# 辅助表（跨表引用目标）+ 主表
sid2 = cs.create_sheet("辅助表", conn=conn)
cs.save_content(sid2, cs.default_content(3, 2, ) | {"cells": {"0,0": {"raw": "100", "kind": "number"}}},
                conn=conn)
sid = cs.create_sheet("导出表", conn=conn)
c = cs.default_content(6, 2)
c["params"] = {"k": 0.3}
c["cells"] = {
    "0,0": {"raw": "100", "kind": "number"},                                   # A1 数字
    "1,0": {"raw": "=A1*2", "kind": "formula"},                                # A2 纯公式 → 保留
    "2,0": {"raw": '=DATA("周立生","业务收入",2025,3)', "kind": "formula"},      # A3 DATA → 填值 6000
    "3,0": {"raw": '=A1*2+DATA("周立生","业务收入",2025,3)', "kind": "formula"},  # A4 混合 → 填值 6200
    "4,0": {"raw": "=辅助表!A1", "kind": "formula"},                            # A5 跨表 → 填值 100
    "5,0": {"raw": '=PARAM("k")*100', "kind": "formula"},                      # A6 PARAM → 填值 30
}
cs.save_content(sid, c, conn=conn)

from app.exporter.calc_export import (  # noqa: E402
    export_sheet, should_keep_formula, suggest_filename,
)

fails, OK = [], 0


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        fails.append(f"{label} {detail}".strip())


# ===== 纯逻辑：保留公式判定 =====
check("纯公式保留", should_keep_formula("=A1*2") is True)
check("SUM 保留", should_keep_formula("=SUM(A1:A10)") is True)
check("DATA 填值", should_keep_formula('=DATA("x","y",2025)') is False)
check("PARAM 填值", should_keep_formula('=PARAM("k")') is False)
check("混合填值", should_keep_formula('=A1+DATA("x","y",2025)') is False)
# 阶段5 C：跨表引用**保留**为真公式，前提是被引用表一起导出（见 export_sheet 闭包）
check("跨表保留", should_keep_formula("=Sheet2!A1") is True)
check("跨表 被引用表不在集合内填值",
      should_keep_formula("=Sheet2!A1", {"主表"}) is False)
check("N2 #REF! 不写公式", should_keep_formula("=#REF!") is False)
check("非公式不保留", should_keep_formula("100") is False)

# ===== 带公式版导出 =====
f1 = export_sheet(sid, str(TMP.parent / "带公式.xlsx"), with_formula=True, conn=conn)
from openpyxl import load_workbook  # noqa: E402

wb = load_workbook(f1)
ws = wb["导出表"]
check("A1 数字", ws["A1"].value == 100, f"got={ws['A1'].value}")
check("A2 保留公式", ws["A2"].value == "=A1*2", f"got={ws['A2'].value}")
check("A3 DATA 填值 6000", ws["A3"].value == 6000, f"got={ws['A3'].value}")
check("A4 混合填值 6200", ws["A4"].value == 6200, f"got={ws['A4'].value}")
# 阶段5 C：跨表引用保留为真公式，且「辅助表」作为被引用表一起导出
check("A5 跨表保留公式", ws["A5"].value == "=辅助表!A1", f"got={ws['A5'].value}")
check("辅助表一并导出", "辅助表" in wb.sheetnames, f"got={wb.sheetnames}")
check("A6 PARAM 填值 30", ws["A6"].value == 30, f"got={ws['A6'].value}")

# ===== 不带公式版导出 =====
f2 = export_sheet(sid, str(TMP.parent / "仅值.xlsx"), with_formula=False, conn=conn)
ws2 = load_workbook(f2)["导出表"]
check("仅值 A2=200", ws2["A2"].value == 200, f"got={ws2['A2'].value}")
check("仅值 A3=6000", ws2["A3"].value == 6000, f"got={ws2['A3'].value}")
check("仅值 A5=100", ws2["A5"].value == 100, f"got={ws2['A5'].value}")

# ===== 文件名 =====
check("文件名 带公式", suggest_filename("导出表", True) == "导出表.xlsx",
      f"got={suggest_filename('导出表', True)}")
check("文件名 仅值", suggest_filename("导出表", False) == "导出表（仅值）.xlsx",
      f"got={suggest_filename('导出表', False)}")

conn.close()
print(f"SMOKE PASS {OK}" if not fails else "SMOKE FAILED:\n" + "\n".join(fails))
sys.exit(0 if not fails else 1)
