"""calc_eval（求值编排层）单元测试 — 内存库全链路。

运行：python tests/test_calc_eval.py
覆盖：跨表引用 / PARAM / DATA 内置指标 / 自定义指标(含 $占位+全年) /
嵌套指标 / 指标循环→#REF! / sheet_grid / 单元格分类入 content。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_calc_data import make_conn, seed  # noqa: E402  复用业务种子数据
from app.engine import calc_sheet as cs  # noqa: E402
from app.engine.calc_eval import CalcEvaluator  # noqa: E402
from app.engine.calc_formula import is_err  # noqa: E402

OK, FAILS = 0, []


def check(label, got, want):
    global OK
    if isinstance(want, str) and want.startswith("#"):
        if is_err(got) and got.code == want:
            OK += 1
        else:
            FAILS.append(f"{label}: got={got!r} want={want}")
    elif isinstance(want, str):
        if got == want:
            OK += 1
        else:
            FAILS.append(f"{label}: got={got!r} want={want}")
    elif is_err(got):
        FAILS.append(f"{label}: got={got!r} want={want}")
    elif abs(float(got) - float(want)) <= 0.011:
        OK += 1
    else:
        FAILS.append(f"{label}: got={got!r} want={want}")


def content(cells, params=None, rows=6, cols=3):
    return {"version": 1, "rows": rows, "cols": cols,
            "cells": cells, "params": params or {},
            "col_headers": [], "row_headers": []}


def main() -> int:
    global OK
    conn = make_conn()
    seed(conn)

    # ---- 两张计算表：主表(公式+参数) / 辅助表(跨表引用) ----
    mid = cs.create_sheet("主表", conn=conn)
    sid = cs.create_sheet("辅助表", conn=conn)
    main_cells = {
        "0,0": {"raw": "周立生", "kind": "text"},
        "1,0": {"raw": '=DATA(A1,"业务收入",2025,3)', "kind": "formula"},
        "2,0": {"raw": '=PARAM("提成比例")*100', "kind": "formula"},
        "1,1": {"raw": "=辅助表!A1*2", "kind": "formula"},
    }
    cs.save_content(mid, content(main_cells, {"提成比例": 0.3}), conn=conn)
    cs.save_content(sid, content({"0,0": {"raw": "100", "kind": "number"}}), conn=conn)

    ev = CalcEvaluator(conn, mid)

    # ===== 1. DATA 内置指标（经网格供给器） =====
    check("DATA 业务收入 3月", ev.cell_value("主表", 1, 0), 6000)
    check("公式引用 DATA 格 ×2", ev.cell_value("主表", 1, 1), 200)
    check("PARAM×100", ev.cell_value("主表", 2, 0), 30)
    check("文本格", ev.cell_value("主表", 0, 0), "周立生")

    # ===== 2. 跨表引用 =====
    check("辅助表 A1", ev.cell_value("辅助表", 0, 0), 100)
    check("辅助表 引用主表公式", ev.evaluate_in_cur("=主表!A2+1"), 6001)

    # ===== 3. 自定义指标（B 层） =====
    conn.execute("INSERT INTO calc_indicator(name,definition,note) VALUES(?,?,?)",
                 ("净分成", 'DATA($职工,"业务收入",$年,$月)*PARAM("提成比例")',
                  "业务收入×提成比例"))
    conn.execute("INSERT INTO calc_indicator(name,definition,note) VALUES(?,?,?)",
                 ("双倍净分成", 'DATA($职工,"净分成",$年,$月)*2', "嵌套指标"))
    conn.commit()
    ev2 = CalcEvaluator(conn, mid)   # 重新载入指标
    check("自定义指标 3月=6000×0.3", ev2.indicator_value("周立生", "净分成", 2025, 3), 1800)
    check("嵌套指标 3月", ev2.indicator_value("周立生", "双倍净分成", 2025, 3), 3600)

    # ===== 4. 指标循环 → #REF! =====
    conn.execute("INSERT INTO calc_indicator(name,definition) VALUES(?,?)",
                 ("环A", 'DATA($职工,"环B",$年,$月)'))
    conn.execute("INSERT INTO calc_indicator(name,definition) VALUES(?,?)",
                 ("环B", 'DATA($职工,"环A",$年,$月)'))
    conn.commit()
    ev3 = CalcEvaluator(conn, mid)
    try:
        ev3.indicator_value("周立生", "环A", 2025, 3)
        FAILS.append("指标循环未报错")
    except Exception as e:
        if "循环" in str(e):
            OK += 1
        else:
            FAILS.append(f"循环错误信息异常: {e}")
    check("DATA 引用循环指标→#REF!",
          ev3.evaluate_in_cur('=DATA("周立生","环A",2025,3)'), "#REF!")

    # ===== 5. 未知指标 → #REF! =====
    check("DATA 未知指标→#REF!",
          ev3.evaluate_in_cur('=DATA("周立生","不存在的指标",2025,3)'), "#REF!")

    # ===== 6. sheet_grid =====
    grid = ev.sheet_grid("主表")
    if len(grid) == 6 and len(grid[0]) == 3:
        OK += 1
    else:
        FAILS.append(f"grid 形状异常: {len(grid)}x{len(grid[0]) if grid else 0}")
    check("grid[1][0]=DATA值", grid[1][0], 6000)

    # ===== 7. 单元格写入 kind 自动分类 =====
    from app.engine.calc_formula import classify_cell
    check("classify=公式", classify_cell('=DATA("x","y",1)'), "formula")

    conn.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
