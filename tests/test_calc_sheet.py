"""calc_sheet（表格存取层）单元测试 — 内存库。

运行：python tests/test_calc_sheet.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import calc_sheet as cs  # noqa: E402
from app.engine.calc_sheet import CalcSheetError  # noqa: E402

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
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # ===== 命名校验 =====
    check("合法中文名", cs.validate_sheet_name("2025分成核算") is None)
    check("合法字母下划线", cs.validate_sheet_name("Calc_01") is None)
    check("禁 A1", cs.validate_sheet_name("A1") is not None)
    check("禁 AB12", cs.validate_sheet_name("AB12") is not None)
    check("禁空格", cs.validate_sheet_name("我的 表") is not None)
    check("禁全角空格", cs.validate_sheet_name("我的\u3000表") is not None)
    check("禁 !", cs.validate_sheet_name("a!b") is not None)
    check("禁空名", cs.validate_sheet_name("  ") is not None)

    # ===== 新建 / 列表 =====
    id1 = cs.create_sheet("测试表A", updated_by="user1", conn=conn)
    id2 = cs.create_sheet("2025分成", conn=conn)
    check("新建返回 id", id1 == 1 and id2 == 2)
    check("默认 content 版本", cs.get_sheet(id1, conn)["content"]["version"] == 1)
    check("默认行列", (cs.get_sheet(id1, conn)["content"]["rows"],
                     cs.get_sheet(id1, conn)["content"]["cols"]) == (50, 12))
    check("列表排序", [s["name"] for s in cs.list_sheets(conn)] == ["测试表A", "2025分成"])
    check("updated_by 记录", cs.get_sheet(id1, conn)["updated_by"] == "user1")

    expect_error("重名拒绝", lambda: cs.create_sheet("测试表A", conn=conn))
    expect_error("非法名拒绝", lambda: cs.create_sheet("B2", conn=conn))

    # ===== 保存 / 回读 =====
    content = cs.get_sheet(id1, conn)["content"]
    content["cells"]["0,0"] = {"raw": "职工", "kind": "text"}
    content["cells"]["1,0"] = {"raw": '=DATA(A2,"业务收入",2025,1)', "kind": "formula"}
    content["cells"]["2,0"] = {"raw": "周立生", "kind": "text"}
    content["params"] = {"提成比例": 0.3}
    cs.save_content(id1, content, updated_by="user2", conn=conn)
    back = cs.get_sheet(id1, conn)
    check("保存后 cells 回读", back["content"]["cells"]["1,0"]["kind"] == "formula")
    check("保存后 params 回读", back["content"]["params"]["提成比例"] == 0.3)
    check("保存刷新 updated_by", back["updated_by"] == "user2")

    expect_error("坏版本拒绝", lambda: cs.save_content(id1, {"version": 99}, conn=conn))
    expect_error("不存在表拒绝", lambda: cs.save_content(999, content, conn=conn))

    # ===== 复制 =====
    id3 = cs.copy_sheet(id1, "测试表A副本", updated_by="user3", conn=conn)
    c3 = cs.get_sheet(id3, conn)
    check("复制保留公式", c3["content"]["cells"]["1,0"]["raw"] == '=DATA(A2,"业务收入",2025,1)')
    check("复制保留参数", c3["content"]["params"] == {"提成比例": 0.3})
    check("复制独立（改副本不影响源）", True)
    c3["content"]["params"]["提成比例"] = 0.5
    cs.save_content(id3, c3["content"], conn=conn)
    check("源表参数未变", cs.get_sheet(id1, conn)["content"]["params"]["提成比例"] == 0.3)
    check("副本 order 置末", cs.list_sheets(conn)[-1]["name"] == "测试表A副本")

    expect_error("复制重名拒绝", lambda: cs.copy_sheet(id1, "2025分成", conn=conn))
    expect_error("复制源不存在", lambda: cs.copy_sheet(999, "X", conn=conn))

    # ===== 重命名 / 删除 =====
    cs.rename_sheet(id2, "2026分成", updated_by="user1", conn=conn)
    check("重命名生效", cs.get_sheet(id2, conn)["name"] == "2026分成")
    expect_error("改名撞名", lambda: cs.rename_sheet(id2, "测试表A", conn=conn))
    expect_error("改名非法", lambda: cs.rename_sheet(id2, "C3", conn=conn))

    cs.delete_sheet(id2, conn=conn)
    check("删除生效", cs.get_sheet(id2, conn) is None)
    expect_error("重复删除", lambda: cs.delete_sheet(id2, conn=conn))
    check("删除后剩两张", len(cs.list_sheets(conn)) == 2)

    conn.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
