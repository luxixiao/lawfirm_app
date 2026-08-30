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

import json
import re
from typing import Dict, List, Optional

from app.db import get_conn

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


def get_sheet(sheet_id: int, conn=None) -> Optional[Dict]:
    own = conn is None
    conn = conn or get_conn()
    try:
        r = conn.execute(
            """SELECT id, name, sheet_order, updated_by, created, updated_at, content
               FROM calc_sheet WHERE id=?""", (sheet_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["content"] = json.loads(d["content"] or "{}")
        except json.JSONDecodeError:
            d["content"] = default_content()
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


def save_content(sheet_id: int, content: Dict, updated_by: str = "", conn=None) -> None:
    """整表保存（编辑即存）；同时刷新 updated_by/updated_at。"""
    own = conn is None
    conn = conn or get_conn()
    try:
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
