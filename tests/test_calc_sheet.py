"""calc_sheet（表格存取层）单元测试 — 内存库。

运行：python tests/test_calc_sheet.py
"""
import os
import sqlite3
import sys
import tempfile
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

    # ===== P0-4：content 损坏不得静默落空表，也不得被空表覆盖写回 =====
    _tmp = tempfile.mkdtemp(prefix="lawfirm_anchor_")
    _old_root = os.environ.get("LAWFIRM_ANCHOR_ROOT")
    os.environ["LAWFIRM_ANCHOR_ROOT"] = _tmp          # 备份落临时目录，不污染本机
    try:
        id4 = cs.create_sheet("损坏表", conn=conn)
        good = cs.get_sheet(id4, conn)["content"]
        good["cells"]["0,0"] = {"raw": "重要数据", "kind": "text"}
        cs.save_content(id4, good, conn=conn)
        # 模拟 Seafile 同步截断 / 半写入：把 content 写坏
        bad = '{"version":1,"cells":{"0,0":{"raw":"重要数据"'
        conn.execute("UPDATE calc_sheet SET content=? WHERE id=?", (bad, id4))
        conn.commit()

        d = cs.get_sheet(id4, conn)
        check("P0-4 损坏表带 content_corrupt 标记（不再静默空表）",
              d.get("content_corrupt") is True)
        check("P0-4 损坏表原串已落库外备份", bool(d.get("content_backup")))
        check("P0-4 备份文件真实存在",
              bool(d.get("content_backup")) and os.path.exists(d["content_backup"]))

        expect_error("P0-4 默认拒绝覆盖损坏内容",
                     lambda: cs.save_content(id4, cs.default_content(), conn=conn))
        raw_after = conn.execute(
            "SELECT content FROM calc_sheet WHERE id=?", (id4,)).fetchone()["content"]
        check("P0-4 拒绝保存后库内原串未被改写", raw_after == bad)

        cs.save_content(id4, cs.default_content(), conn=conn, allow_overwrite_broken=True)
        check("P0-4 显式强制保存可写",
              cs.get_sheet(id4, conn).get("content_corrupt") is None)
    finally:
        if _old_root is None:
            os.environ.pop("LAWFIRM_ANCHOR_ROOT", None)
        else:
            os.environ["LAWFIRM_ANCHOR_ROOT"] = _old_root

    # ===== 阶段3 G2：插入/删除行、列（结构变换纯函数 + 引用重写） =====
    def _raw(c, r, col):
        cell = c.get("cells", {}).get(f"{r},{col}")
        return cell.get("raw") if cell else None

    # --- 行插入（at=2, delta=1）：数据格后移、公式扩张、绝对不动、跨表不动 ---
    b1 = cs.default_content(rows=6, cols=6)
    b1["row_headers"] = [f"h{i}" for i in range(6)]
    b1["cells"]["0,0"] = {"raw": "标题", "kind": "text"}              # 行0（<at）不动
    b1["cells"]["4,3"] = {"raw": "数据", "kind": "text"}              # 行4（>=at）下移→行5
    b1["cells"]["0,1"] = {"raw": "=A3", "kind": "formula"}            # 引用 row2(>=at)→下移
    b1["cells"]["5,0"] = {"raw": "=$A$1", "kind": "formula"}          # 绝对不动
    b1["cells"]["4,0"] = {"raw": "=Sheet2!A1+A1", "kind": "formula"}  # 跨表不动+本地A1(<at)不动
    nr = cs.insert_row(b1, 2, 1)
    check("G2 行插入 rows+1", nr["rows"] == 7)
    check("G2 行插入 数据格<at不动 0,0 仍=标题", _raw(nr, 0, 0) == "标题")
    check("G2 行插入 数据格>=at后移 4,3→5,3", _raw(nr, 5, 3) == "数据")
    check("G2 行插入 公式扩张 =A3→=A4", _raw(nr, 0, 1) == "=A4")
    check("G2 行插入 绝对引用不动 =$A$1", _raw(nr, 6, 0) == "=$A$1")
    check("G2 行插入 跨表引用不动+本地A1不动", _raw(nr, 5, 0) == "=Sheet2!A1+A1")
    check("G2 行插入 纯函数：原表未变", b1["rows"] == 6 and _raw(b1, 0, 1) == "=A3")
    check("G2 行插入 row_headers 同步（at 处补空位）",
          nr["row_headers"] == ["h0", "h1", None, "h2", "h3", "h4", "h5"])

    # --- 行删除（at=2, delta=1）：引用命中删除带→#REF!，其下引用上移 ---
    b2 = cs.default_content(rows=6, cols=3)
    b2["cells"]["0,0"] = {"raw": "=A3", "kind": "formula"}   # 引用 row2（落在删除带）→ #REF!
    b2["cells"]["0,1"] = {"raw": "=A4", "kind": "formula"}   # 引用 row3(>=at+1)→上移到 A3
    dr = cs.delete_row(b2, 2, 1)
    check("G2 行删除 rows-1", dr["rows"] == 5)
    check("G2 行删除 命中删除带→#REF!", _raw(dr, 0, 0) in ("=#REF!", "#REF!"))
    check("G2 行删除 其下引用上移 =A4→=A3", _raw(dr, 0, 1) == "=A3")

    # --- 列插入（at=1, delta=1）：公式列轴扩张、绝对列不动 ---
    b3 = cs.default_content(rows=3, cols=4)
    b3["cells"]["0,0"] = {"raw": "=B2", "kind": "formula"}     # 列1(>=at)→右移成 C，格在 col0 不动
    b3["cells"]["0,1"] = {"raw": "=$A1", "kind": "formula"}    # 列绝对不动，格随插列移到 col2
    b3["cells"]["0,2"] = {"raw": "=B$1", "kind": "formula"}    # 行绝对列相对：B(列1)→C，格移到 col3
    nc = cs.insert_col(b3, 1, 1)
    check("G2 列插入 cols+1", nc["cols"] == 5)
    check("G2 列插入 公式扩张 =B2→=C2", _raw(nc, 0, 0) == "=C2")
    check("G2 列插入 列绝对不动 =$A1（格移到 col2）", _raw(nc, 0, 2) == "=$A1")
    check("G2 列插入 行绝对列相对 =B$1→=C$1（格移到 col3）", _raw(nc, 0, 3) == "=C$1")

    # --- 列删除（at=1, delta=1）：命中删除带→#REF!，其右引用左移 ---
    b4 = cs.default_content(rows=3, cols=4)
    b4["cells"]["0,0"] = {"raw": "=B2", "kind": "formula"}   # 列1（落在删除带）→ #REF!，格在 col0 存
    b4["cells"]["0,2"] = {"raw": "=C3", "kind": "formula"}   # 列2(>=at+1)→左移成 B，格移到 col1
    dc = cs.delete_col(b4, 1, 1)
    check("G2 列删除 cols-1", dc["cols"] == 3)
    check("G2 列删除 命中删除带→#REF!", _raw(dc, 0, 0) in ("=#REF!", "#REF!"))
    check("G2 列删除 其右引用左移 =C3→=B3（格移到 col1）", _raw(dc, 0, 1) == "=B3")

    # --- 区域收缩：删中间列 → 区域两端收紧 ---
    b5 = cs.default_content(rows=2, cols=4)
    b5["cells"]["0,0"] = {"raw": "=SUM(A1:C1)", "kind": "formula"}  # 删列1(B) → A1:B1
    dc_rng = cs.delete_col(b5, 1, 1)
    check("G2 列删除 区域收缩 =SUM(A1:C1)→=SUM(A1:B1)",
          _raw(dc_rng, 0, 0) == "=SUM(A1:B1)")

    # --- D1 最小尺寸守卫：删到只剩 1 行/列不崩、内容保留 ---
    one = cs.default_content(rows=1, cols=1)
    one["cells"]["0,0"] = {"raw": "x", "kind": "text"}
    check("G2 D1 删末行被守卫：仍剩 1 行", cs.delete_row(one, 0, 1)["rows"] == 1)
    check("G2 D1 删末行内容不变", _raw(cs.delete_row(one, 0, 1), 0, 0) == "x")
    check("G2 D1 删末列被守卫：仍剩 1 列", cs.delete_col(one, 0, 1)["cols"] == 1)

    # --- D2 at 越界夹取：插到末行之后=追加，且不丢首格 ---
    oob = cs.insert_row(b1, 999, 1)
    check("G2 D2 at 越界夹取到末行后", oob["rows"] == 7)
    check("G2 D2 越界插入不丢首格", _raw(oob, 0, 0) == "标题")

    # --- 撤销链路：BulkCommand 整 content 快照（阶段2 复用，calc_undo.py 零改动） ---
    from app.engine.calc_undo import BulkCommand
    bk = cs.default_content(rows=4, cols=4)
    bk["cells"]["1,1"] = {"raw": "=B2", "kind": "formula"}
    after = cs.insert_col(bk, 1, 1)
    cmd = BulkCommand(before=bk, after=after)
    check("G2 BulkCommand apply 幂等于 after", cmd.apply(bk)["cols"] == 5)
    reverted = cmd.revert(after)
    check("G2 BulkCommand revert 回到 before", reverted["cols"] == 4
          and _raw(reverted, 1, 1) == "=B2")

    # ===== 阶段4 G4：填充映射与块位移（纯逻辑） =====
    # 单格源（1x1）：逐格独立偏移
    f1 = {"0,0": {"raw": "=A1", "kind": "formula"}}
    m = list(cs.fill_map((0, 0, 1, 1), (0, 0, 3, 1)))
    check("G4 fill_map 单格源 3 目标", len(m) == 3)
    fc = cs.fill_cells(f1, (0, 0, 1, 1), (0, 0, 3, 1))
    check("G4 单格源下填 =A1→=A2/=A3", fc.get("1,0") == "=A2" and fc.get("2,0") == "=A3")
    # 右填：=A1 从 B1 拖到 C1 → =B1
    fc2 = cs.fill_cells(f1, (0, 0, 1, 1), (0, 0, 1, 2))
    check("G4 单格源右填 =A1→=B1（C1 列）", fc2.get("0,1") == "=B1")
    # 块源（2x2）取模重复：源 B2:C3，填充到 4x2 目标
    f2 = {"1,1": {"raw": "=B2", "kind": "formula"},
          "1,2": {"raw": "=C2", "kind": "formula"},
          "2,1": {"raw": "=B3", "kind": "formula"},
          "2,2": {"raw": "=C3", "kind": "formula"}}
    fc3 = cs.fill_cells(f2, (1, 1, 2, 2), (1, 1, 4, 2))
    check("G4 块源 前两行原样", fc3.get("1,1") == "=B2" and fc3.get("2,2") == "=C3")
    check("G4 块源 后两行整块平移（下移2行）",
          fc3.get("3,1") == "=B4" and fc3.get("4,2") == "=C5")
    # D3：空白源格跳过（不误清目标已有数据）
    f3 = {"0,0": {"raw": "=A1", "kind": "formula"}}  # 2x2 源但只有 0,0 有值
    fc4 = cs.fill_cells(f3, (0, 0, 2, 2), (0, 0, 2, 2))
    check("G4 D3 空白源格跳过", list(fc4.keys()) == ["0,0"] and fc4["0,0"] == "=A1")
    # 绝对列在块位移中不动
    f4 = {"0,0": {"raw": "=$A1", "kind": "formula"}}
    fc5 = cs.fill_cells(f4, (0, 0, 1, 1), (0, 0, 1, 2))  # 右填 1 格
    check("G4 绝对列右填不动 =$A1→=$A1", fc5.get("0,1") == "=$A1")
    # 越界→#REF!：单格源左填越界（B2 的 =A1 拖到 A2 → 列 -1）
    f5 = {"1,1": {"raw": "=A1", "kind": "formula"}}
    fc6 = cs.fill_cells(f5, (1, 1, 1, 1), (1, 0, 1, 1))
    check("G4 越界填充→#REF!", fc6.get("1,0") == "=#REF!")

    conn.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
