"""分成计算引擎 — calc_sheet 表格存取层（spec: calc_engine_spec.md §2.1/§6）

- 存储 = 方案甲 JSON 整表：calc_sheet.content 一个字段存整张网格。
- 复制表 = 克隆 content JSON + 改名，公式/引用/参数天然全保留。
- content 结构（version:1）：
    {"version":1, "rows":50, "cols":12,
     "cells":{"r,c":{"raw":..,"kind":..}},       # r,c 均 0 基
     "params":{"名称":值},                        # A 层命名参数
     "col_headers":[...], "row_headers":[...]}
- 命名校验（spec §11.3）：禁单元格模式(A1~ZZZ99999)/空格/!；唯一约束由 DB 兜底。
- updated_by/updated_at：DB 在 Seafile 同步范围，展示"最后编辑人/时间"提示覆盖风险。
- 只读不回写：本层只读写 calc_sheet 自身，绝不触碰业务主表。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from app.db import get_conn
from app.engine.calc_ref_rewrite import shift_refs

CONTENT_VERSION = 1
DEFAULT_ROWS = 50
DEFAULT_COLS = 12

# 表名禁单元格模式（A1 ~ ZZZ99999），禁空格与 !
_CELL_LIKE = re.compile(r"^[A-Za-z]{1,3}[0-9]+$")


class CalcSheetError(Exception):
    """表格存取层业务错误（提示给 UI）。"""


# ---------------------------------------------------------------------------
# 命名校验
# ---------------------------------------------------------------------------

def validate_sheet_name(name: str) -> Optional[str]:
    """合法返回 None；非法返回错误说明。"""
    name = (name or "").strip()
    if not name:
        return "表名不能为空"
    if len(name) > 50:
        return "表名过长（≤50 字符）"
    if " " in name or "\u3000" in name:
        return "表名不能含空格"
    if "!" in name or "'" in name:
        return "表名不能含 ! 或 '"
    if _CELL_LIKE.match(name):
        return "表名不能是单元格引用样式（如 A1、AB12）"
    return None


def default_content(rows: int = DEFAULT_ROWS, cols: int = DEFAULT_COLS) -> Dict:
    return {
        "version": CONTENT_VERSION,
        "rows": rows,
        "cols": cols,
        "cells": {},
        "params": {},
        "col_headers": [],
        "row_headers": [],
    }


def normalize_content(content: Dict) -> Dict:
    """入库前规整：补缺省键、校验 version/rows/cols/cells 结构。"""
    if not isinstance(content, dict):
        raise CalcSheetError("content 必须是对象")
    base = default_content()
    if "version" not in content:
        content["version"] = CONTENT_VERSION
    if content["version"] != CONTENT_VERSION:
        raise CalcSheetError(f"不支持的 content 版本：{content['version']}")
    for k in ("rows", "cols", "cells", "params"):
        if k not in content:
            content[k] = base[k]
    if not isinstance(content["cells"], dict):
        raise CalcSheetError("cells 必须是对象")
    if not isinstance(content["params"], dict):
        raise CalcSheetError("params 必须是对象")
    return content


# ---------------------------------------------------------------------------
# 结构变换（阶段3 G2：插入/删除行、列，引用按 §2.4 真重写）—— 全部返回**新** content
# ---------------------------------------------------------------------------

def _clamp_at(at: int, limit: int) -> int:
    """插入/删除位置夹取到 [0, limit]（D2：越界位置不崩，夹到边界）。"""
    if at < 0:
        return 0
    if at > limit:
        return limit
    return at


def _shift_headers(headers: List, at: int, delta: int, insert: bool) -> List:
    """同步 row_headers/col_headers：插入=在 at 处补 delta 个空位；删除=移除 [at,at+delta)。"""
    new = list(headers)
    if at < 0:
        at = 0
    if insert:
        for _ in range(delta):
            if at <= len(new):
                new.insert(at, None)
            else:
                new.append(None)
    else:
        if at < len(new):
            del new[at:at + delta]
    return new


def _restructure(content: Dict, axis: str, at: int, delta: int, insert: bool) -> Dict:
    """插/删行列的统一内核（返回新 content，绝不原地改）。

    - 数据格：按 `>=at` 后移 / `[at,at+delta)` 删除 重映射 cell-key。
    - 公式格：先对内部引用跑 `shift_refs`（扩张/收缩/绝对不动/跨表不动/#REF!），
      再对**自身位置**按同规则重映射 key。
    - D1 最小尺寸守卫：删除后至少留 1 行/列，delta 过大时夹掉多余删除量。
    - D2 `at` 越界夹取到 [0, limit]。
    """
    if delta <= 0:
        return copy.deepcopy(content)
    content = copy.deepcopy(content)
    rows = int(content.get("rows") or 0)
    cols = int(content.get("cols") or 0)

    # D2：位置夹取
    if axis == "row":
        limit = rows
    else:
        limit = cols
    at = _clamp_at(at, limit)

    # D1：删除后至少留 1 行/列
    if not insert and delta >= limit:
        delta = max(0, limit - 1)
        if delta <= 0:
            return content  # 已是最末 1 行/列，无物可删

    new_cells: Dict[str, Dict] = {}
    for key, cell in (content.get("cells") or {}).items():
        r0, c0 = (int(x) for x in key.split(","))
        coord = r0 if axis == "row" else c0
        raw = cell.get("raw", "")
        # 公式格：先重写内部引用
        if isinstance(raw, str) and raw.startswith("="):
            new_raw = shift_refs(raw, axis, at, delta, insert=insert)
        else:
            new_raw = raw
        # 自身位置重映射（与 shift_refs 的 at 语义一致，0 基、无 off-by-one）
        if insert:
            new_coord = coord + delta if coord >= at else coord
        else:
            if at <= coord < at + delta:
                continue  # 落在被删带 → 整格删除
            new_coord = coord - delta if coord >= at + delta else coord
        if axis == "row":
            nr, nc = new_coord, c0
        else:
            nr, nc = r0, new_coord
        new_cells[f"{nr},{nc}"] = {
            "raw": new_raw,
            "kind": cell.get("kind"),
        }

    if axis == "row":
        content["rows"] = rows + delta if insert else rows - delta
        content["row_headers"] = _shift_headers(
            content.get("row_headers", []), at, delta, insert)
    else:
        content["cols"] = cols + delta if insert else cols - delta
        content["col_headers"] = _shift_headers(
            content.get("col_headers", []), at, delta, insert)
    content["cells"] = new_cells
    return content


def insert_row(content: Dict, at: int, delta: int = 1) -> Dict:
    """在第 at 行（0 基）上方插入 delta 行，返回新 content。"""
    return _restructure(content, "row", at, delta, insert=True)


def delete_row(content: Dict, at: int, delta: int = 1) -> Dict:
    """删除第 at 行（0 基）起的 delta 行，返回新 content。"""
    return _restructure(content, "row", at, delta, insert=False)


def insert_col(content: Dict, at: int, delta: int = 1) -> Dict:
    """在第 at 列（0 基）左侧插入 delta 列，返回新 content。"""
    return _restructure(content, "col", at, delta, insert=True)


def delete_col(content: Dict, at: int, delta: int = 1) -> Dict:
    """删除第 at 列（0 基）起的 delta 列，返回新 content。"""
    return _restructure(content, "col", at, delta, insert=False)


# ---------------------------------------------------------------------------
# CRUD（conn 可注入，供内存库单测）
# ---------------------------------------------------------------------------

def list_sheets(conn=None) -> List[Dict]:
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            """SELECT id, name, sheet_order, updated_by, created, updated_at
               FROM calc_sheet ORDER BY sheet_order, id""").fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def _broken_backup_dir() -> Path:
    """损坏 content 的库外备份目录 —— **必须在 Seafile 同步范围之外**。

    DB 本身随 Seafile 跨 3 台 PC 同步（有覆盖写风险），把备份写回 data/ 会被同步
    再覆盖/再损坏，所以放本机库外：
    `LAWFIRM_ANCHOR_ROOT` ＞ `%LOCALAPPDATA%\\lawfirm_app` ＞ `~/.lawfirm_app`。
    """
    root = (os.environ.get("LAWFIRM_ANCHOR_ROOT") or "").strip()
    if not root:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        root = os.path.join(base, "lawfirm_app")
    return Path(root) / "calc_sheet_broken"


def backup_broken_content(sheet_id: int, raw: str) -> str:
    """把无法解析的原始 content 落库外备份（同内容只留一份）。返回路径字符串。"""
    d = _broken_backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1((raw or "").encode("utf-8", "replace")).hexdigest()[:8]
    fp = d / f"calc_sheet_{sheet_id}_{digest}.json"
    if not fp.exists():
        fp.write_text(raw or "", encoding="utf-8")
    return str(fp)


def get_sheet(sheet_id: int, conn=None) -> Optional[Dict]:
    """读取单表。content 无法解析时**不再静默落空表**：

    - 原始串先落库外备份（`backup_broken_content`）；
    - 返回字典带 `content_corrupt=True` 与 `content_backup=<路径>`，由 UI 提示并拒绝编辑；
    - `save_content` 另有一道守卫（双保险），单凭 UI 漏判也不会覆盖原数据。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        r = conn.execute(
            """SELECT id, name, sheet_order, updated_by, created, updated_at, content
               FROM calc_sheet WHERE id=?""", (sheet_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        raw = d["content"] or ""
        try:
            d["content"] = json.loads(raw or "{}")
        except json.JSONDecodeError:
            d["content"] = default_content()
            d["content_corrupt"] = True
            try:
                d["content_backup"] = backup_broken_content(sheet_id, raw)
            except OSError:
                d["content_backup"] = ""
        return d
    finally:
        if own:
            conn.close()


def create_sheet(name: str, updated_by: str = "", rows: int = DEFAULT_ROWS,
                 cols: int = DEFAULT_COLS, conn=None) -> int:
    own = conn is None
    conn = conn or get_conn()
    try:
        err = validate_sheet_name(name)
        if err:
            raise CalcSheetError(err)
        dup = conn.execute("SELECT id FROM calc_sheet WHERE name=?", (name,)).fetchone()
        if dup:
            raise CalcSheetError(f"表名已存在：{name}")
        order_row = conn.execute(
            "SELECT COALESCE(MAX(sheet_order),0)+1 AS nxt FROM calc_sheet").fetchone()
        cur = conn.execute(
            """INSERT INTO calc_sheet(name, sheet_order, updated_by, content)
               VALUES(?,?,?,?)""",
            (name, order_row["nxt"], updated_by,
             json.dumps(default_content(rows, cols), ensure_ascii=False)))
        conn.commit()
        return cur.lastrowid
    finally:
        if own:
            conn.close()


def save_content(sheet_id: int, content: Dict, updated_by: str = "", conn=None,
                 allow_overwrite_broken: bool = False) -> None:
    """整表保存（编辑即存）；同时刷新 updated_by/updated_at。

    P0-4 守卫：库中现有 content **非空且无法解析**时，**默认拒绝保存**。
    原因：`get_sheet` 遇损坏会返回空表（已带 content_corrupt 标记），若仍允许保存，
    用户以为"打开错了表"随手改一格就会把空表写回 → 原内容永久丢失且无痕迹。
    确需放弃原内容时显式传 `allow_overwrite_broken=True`（UI 走「强制保存」）。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        row = conn.execute("SELECT content FROM calc_sheet WHERE id=?", (sheet_id,)).fetchone()
        if row is None:
            raise CalcSheetError(f"表不存在：id={sheet_id}")
        raw = row["content"] or ""
        if raw.strip() and not allow_overwrite_broken:
            try:
                json.loads(raw)
            except json.JSONDecodeError:
                try:
                    path = backup_broken_content(sheet_id, raw)
                except OSError:
                    path = ""
                raise CalcSheetError(
                    "该表在库中的内容已损坏（无法解析 JSON），为避免覆盖原始数据已拒绝保存。"
                    + (f"\n原始内容已备份至：{path}" if path else "")
                    + "\n请先从备份恢复；确认放弃原内容时请走「强制保存」。")
        normalize_content(content)
        cur = conn.execute(
            """UPDATE calc_sheet
               SET content=?, updated_by=?, updated_at=datetime('now','localtime')
               WHERE id=?""",
            (json.dumps(content, ensure_ascii=False), updated_by, sheet_id))
        if cur.rowcount == 0:
            raise CalcSheetError(f"表不存在：id={sheet_id}")
        conn.commit()
    finally:
        if own:
            conn.close()


def copy_sheet(src_id: int, new_name: str, updated_by: str = "", conn=None) -> int:
    """克隆 content JSON（公式/引用/参数全保留），改名，order 置末。"""
    own = conn is None
    conn = conn or get_conn()
    try:
        src = conn.execute("SELECT name, content FROM calc_sheet WHERE id=?",
                           (src_id,)).fetchone()
        if not src:
            raise CalcSheetError(f"源表不存在：id={src_id}")
        err = validate_sheet_name(new_name)
        if err:
            raise CalcSheetError(err)
        dup = conn.execute("SELECT id FROM calc_sheet WHERE name=?", (new_name,)).fetchone()
        if dup:
            raise CalcSheetError(f"表名已存在：{new_name}")
        order_row = conn.execute(
            "SELECT COALESCE(MAX(sheet_order),0)+1 AS nxt FROM calc_sheet").fetchone()
        cur = conn.execute(
            """INSERT INTO calc_sheet(name, sheet_order, updated_by, content)
               VALUES(?,?,?,?)""",
            (new_name, order_row["nxt"], updated_by, src["content"]))
        conn.commit()
        return cur.lastrowid
    finally:
        if own:
            conn.close()


def rename_sheet(sheet_id: int, new_name: str, updated_by: str = "", conn=None) -> None:
    own = conn is None
    conn = conn or get_conn()
    try:
        err = validate_sheet_name(new_name)
        if err:
            raise CalcSheetError(err)
        dup = conn.execute("SELECT id FROM calc_sheet WHERE name=? AND id<>?",
                           (new_name, sheet_id)).fetchone()
        if dup:
            raise CalcSheetError(f"表名已存在：{new_name}")
        cur = conn.execute(
            """UPDATE calc_sheet SET name=?, updated_by=?,
               updated_at=datetime('now','localtime') WHERE id=?""",
            (new_name, updated_by, sheet_id))
        if cur.rowcount == 0:
            raise CalcSheetError(f"表不存在：id={sheet_id}")
        conn.commit()
    finally:
        if own:
            conn.close()


def delete_sheet(sheet_id: int, conn=None) -> None:
    own = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.execute("DELETE FROM calc_sheet WHERE id=?", (sheet_id,))
        if cur.rowcount == 0:
            raise CalcSheetError(f"表不存在：id={sheet_id}")
        conn.commit()
    finally:
        if own:
            conn.close()
