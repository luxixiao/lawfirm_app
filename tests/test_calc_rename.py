"""calc_sheet 重命名表 + 跨表引用重写（阶段5 G10）单元测试 — 内存库。

覆盖：
- 纯逻辑 `rename_sheet_refs`：AST 改写（本地/他表/区域/绝对/函数内）、
  **B1 两个坑**（前缀误伤 `Old2!`、字符串字面量 `="Old!A1"`）、解析失败原样返回、
  `Sheet!PARAM(...)` 表前缀。
- content 级纯函数 `rename_sheet_refs_in_content`：只动公式格、不就地改。
- DB 层 `rename_sheet`：单事务改名 + 全库重写、返回改动表数、无变化表不刷 updated_at、
  撞名/非法名拒绝、**B2 损坏表预检整单中止（原子性：名与引用都不变）**。

运行：python tests/test_calc_rename.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import calc_sheet as cs  # noqa: E402
from app.engine.calc_sheet import CalcSheetError  # noqa: E402
from app.engine.calc_ref_rewrite import rename_sheet_refs  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def expect_error(label, fn):
    try:
        fn()
        FAILS.append(f"{label}: 未抛出 CalcSheetError")
    except CalcSheetError:
        global OK
        OK += 1


def main() -> int:
    # ===== 纯逻辑：rename_sheet_refs（AST 改写，非文本替换）=====
    check("改跨表单格", rename_sheet_refs("=Old!A1", "Old", "New") == "=New!A1")
    check("本地引用不动", rename_sheet_refs("=A1", "Old", "New") == "=A1")
    check("他表引用不动", rename_sheet_refs("=Other!A1", "Old", "New") == "=Other!A1")
    check("跨表区域改写",
          rename_sheet_refs("=SUM(Old!A1:B2)", "Old", "New") == "=SUM(New!A1:B2)")
    check("混合公式只改命中",
          rename_sheet_refs("=Old!A1+A1+Other!B2", "Old", "New") == "=New!A1+A1+Other!B2")
    check("多处命中", rename_sheet_refs("=Old!A1+Old!B1", "Old", "New") == "=New!A1+New!B1")
    check("跨表内绝对引用保留",
          rename_sheet_refs("=Old!$A$1", "Old", "New") == "=New!$A$1")
    check("函数参数内引用",
          rename_sheet_refs("=ROUND(Old!A1,2)", "Old", "New") == "=ROUND(New!A1,2)")
    # --- B1：纯文本替换 `Old!`→`New!` 会踩的两个坑，AST 方案必须都躲开 ---
    check("B1 前缀不误伤 Old2!A1",
          rename_sheet_refs("=Old2!A1", "Old", "New") == "=Old2!A1")
    check("B1 字符串字面量不误改",
          rename_sheet_refs('="Old!A1"', "Old", "New") == '="Old!A1"')
    # --- 表前缀参数 Sheet!PARAM("x") ---
    check("Sheet!PARAM 表前缀改名",
          rename_sheet_refs('=Old!PARAM("x")', "Old", "New") == '=New!PARAM("x")')
    check("Sheet!PARAM 他表不动",
          rename_sheet_refs('=Other!PARAM("x")', "Old", "New") == '=Other!PARAM("x")')
    # --- 防御 ---
    check("解析失败原样返回", rename_sheet_refs("=1+", "Old", "New") == "=1+")
    # 裸引用（无 '=' 前缀）同样改写 —— 与 translate_refs 的 _rewrite 骨架保持一致；
    # 真实调用侧 rename_sheet_refs_in_content 只用 startswith("=") 的格，文本格不受影响。
    check("裸引用也改写（与 translate_refs 一致）",
          rename_sheet_refs("Old!A1", "Old", "New") == "New!A1")

    # ===== content 级纯函数 =====
    src = cs.default_content(rows=3, cols=3)
    src["cells"]["0,0"] = {"raw": "=甲表!A1", "kind": "formula"}
    src["cells"]["0,1"] = {"raw": "文本", "kind": "text"}
    src["cells"]["1,0"] = {"raw": "12", "kind": "number"}
    out = cs.rename_sheet_refs_in_content(src, "甲表", "乙表")
    check("content 公式格改写", out["cells"]["0,0"]["raw"] == "=乙表!A1")
    check("content 文本格不动", out["cells"]["0,1"]["raw"] == "文本")
    check("content 数字格不动", out["cells"]["1,0"]["raw"] == "12")
    check("content kind 不变", out["cells"]["0,0"]["kind"] == "formula")
    check("content 纯函数：原 content 未改", src["cells"]["0,0"]["raw"] == "=甲表!A1")

    # ===== DB：单事务改名 + 全库重写 =====
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    a = cs.create_sheet("甲表", conn=conn)
    b = cs.create_sheet("乙表", conn=conn)
    c = cs.create_sheet("丙表", conn=conn)      # 无引用，改名不应动它
    cb = cs.get_sheet(b, conn)["content"]
    cb["cells"]["0,0"] = {"raw": "=甲表!A1", "kind": "formula"}
    cb["cells"]["0,1"] = {"raw": "=A1+甲表!B2", "kind": "formula"}
    cb["cells"]["1,0"] = {"raw": "文本", "kind": "text"}
    cs.save_content(b, cb, conn=conn)
    ca = cs.get_sheet(a, conn)["content"]
    ca["cells"]["0,0"] = {"raw": "=甲表!A1+1", "kind": "formula"}   # 本表自引用
    cs.save_content(a, ca, conn=conn)
    at_c_before = cs.get_sheet(c, conn)["updated_at"]

    n = cs.rename_sheet(a, "甲表2026", updated_by="u", conn=conn)
    check("改名生效", cs.get_sheet(a, conn)["name"] == "甲表2026")
    check("返回被改动表数（本表自引用+他表）", n == 2, f"got={n}")
    nb = cs.get_sheet(b, conn)["content"]["cells"]
    check("他表引用已重写", nb["0,0"]["raw"] == "=甲表2026!A1")
    check("他表混合公式只改命中", nb["0,1"]["raw"] == "=A1+甲表2026!B2")
    check("他表文本格不动", nb["1,0"]["raw"] == "文本")
    na = cs.get_sheet(a, conn)["content"]["cells"]
    check("本表自引用也重写", na["0,0"]["raw"] == "=甲表2026!A1+1")
    check("无关表不刷 updated_at（缩小同步面）",
          cs.get_sheet(c, conn)["updated_at"] == at_c_before)

    expect_error("改名撞名", lambda: cs.rename_sheet(a, "乙表", conn=conn))
    expect_error("改名非法（A1 样式）", lambda: cs.rename_sheet(a, "C3", conn=conn))
    check("拒绝后表名未变", cs.get_sheet(a, conn)["name"] == "甲表2026")
    check("改名同名返回 0（no-op）", cs.rename_sheet(a, "甲表2026", conn=conn) == 0)

    # ===== B2：损坏表预检 → 整单中止，名与引用都不变 =====
    conn.execute("UPDATE calc_sheet SET content=? WHERE id=?", ("{坏掉的JSON", b))
    conn.commit()
    name_before = cs.get_sheet(a, conn)["name"]
    raw_before = cs.get_sheet(a, conn)["content"]["cells"]["0,0"]["raw"]
    expect_error("损坏表存在时拒绝改名", lambda: cs.rename_sheet(a, "甲表2027", conn=conn))
    check("B2 中止后表名未变", cs.get_sheet(a, conn)["name"] == name_before)
    check("B2 中止后引用未变",
          cs.get_sheet(a, conn)["content"]["cells"]["0,0"]["raw"] == raw_before)

    conn.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
