"""工资表原始镜表引擎

- raw_salary 是工资文档 Excel 逐 sheet 逐行 1:1 镜像（保留所有原始列，不归一化），
  与 raw_ledger（发票台账）同构：导入落库、只读查看、可编辑并写 change_log。
- 账期 period 由 import_batch 带出（来自文件名，如 25.1 -> 2025-01），
  故本表不存 period 字段，查询时 LEFT JOIN import_batch。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from app.db import get_conn
from app.engine.change_log import log_change, build_friendly_table

# 可编辑字段（编辑弹窗用）
EDIT_FIELDS = [
    "seq", "staff_name", "share_raw", "salary_raw", "partner_raw",
    "tax_raw", "fund_raw", "net_raw",
    "pension_raw", "medical_raw", "unemployment_raw", "remark",
]

# 金额文本列 -> 对应数值列（编辑文本时同步刷新数值，保证排序/合计正确）
_NUM_OF = {
    "share_raw": "share_num",
    "salary_raw": "salary_num",
    "partner_raw": "partner_num",
    "tax_raw": "tax_num",
    "fund_raw": "fund_num",
    "net_raw": "net_num",
}

# sheet 展示顺序与干净展示名（避免显示 Excel 原始名「后勤 (实)」这类怪名）
SHEET_ORDER = ["lawyer", "partner", "logistics"]

SHEET_LABELS = {
    "lawyer": "聘用律师",
    "partner": "合伙人",
    "logistics": "后勤",
}


def sheet_label(key: str) -> str:
    """sheet_key -> 干净展示名；未知/空返回原值。"""
    return SHEET_LABELS.get(key, key or "")


def _parse_num(txt) -> float:
    """原始文本 -> 数值（取首个数字；非数字如「退休」返回 0.0）。"""
    if txt is None:
        return 0.0
    if isinstance(txt, (int, float)):
        return float(txt)
    m = re.search(r"-?\d[\d,]*\.?\d*", str(txt))
    if not m:
        return 0.0
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def sheet_keys() -> List[Dict]:
    """返回当前库中存在的 sheet（按固定顺序），含干净展示名。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT sheet_key FROM raw_salary "
            "WHERE sheet_key IS NOT NULL AND sheet_key != ''"
        ).fetchall()
    finally:
        conn.close()
    present = {r["sheet_key"] for r in rows}
    out = []
    for k in SHEET_ORDER:
        if k in present:
            out.append({"sheet_key": k, "sheet_name": SHEET_LABELS.get(k, k)})
    # 兜底：库中出现了未预定义的 sheet_key 也列出，避免数据看不见
    for k in sorted(present - set(SHEET_ORDER)):
        out.append({"sheet_key": k, "sheet_name": k})
    return out


def list_raw(sheet_key: str = "", keyword: str = "",
             with_period: bool = True) -> List[Dict]:
    """返回 raw_salary 行（按 sheet/块/行号排序）。

    sheet_key: ""=全部；否则按 lawyer/partner/logistics 过滤。
    keyword:   匹配 编号/姓名/各金额文本/备注（LIKE）。
    with_period: LEFT JOIN import_batch 带出 period（导入账期，如 "2025-01"）。
    """
    clauses, params = [], []
    if sheet_key:
        clauses.append("s.sheet_key = ?")
        params.append(sheet_key)
    if keyword:
        clauses.append(
            "(s.seq LIKE ? OR s.staff_name LIKE ? OR s.share_raw LIKE ? "
            "OR s.salary_raw LIKE ? OR s.partner_raw LIKE ? OR s.remark LIKE ?)"
        )
        params += [f"%{keyword}%"] * 6
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    join = " LEFT JOIN import_batch b ON b.id = s.import_batch_id" if with_period else ""
    sel = "s.*" + (", b.period AS period" if with_period else "")
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT {sel} FROM raw_salary s{join}{where} "
            "ORDER BY s.sheet_key, s.block_no, s.row_no",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_row(rid: int) -> Optional[Dict]:
    conn = get_conn()
    try:
        r = conn.execute("SELECT * FROM raw_salary WHERE id=?", (rid,)).fetchone()
    finally:
        conn.close()
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# 写：编辑（逐字段写 change_log）
# ---------------------------------------------------------------------------
def update_row(rid: int, data: Dict, note: str = "") -> None:
    """编辑一行 raw_salary，逐字段比对并写 change_log（仅记录变了的字段）。

    金额文本变化时同步刷新对应数值列（share_num 等），保证排序与合计正确。
    """
    old = get_row(rid)
    if old is None:
        return
    changes = []
    new_vals = []
    for f in EDIT_FIELDS:
        o = old.get(f) or ""
        n = data.get(f) or ""
        if str(o) != str(n):
            changes.append((f, o, n))
            new_vals.append(n)
        else:
            new_vals.append(o)
    if not changes:
        return
    conn = get_conn()
    try:
        sets = ", ".join(f"{f}=?" for f in EDIT_FIELDS)
        conn.execute(f"UPDATE raw_salary SET {sets} WHERE id=?", new_vals + [rid])
        # 金额文本变化 -> 同步数值列
        for f, _o, n in changes:
            num_col = _NUM_OF.get(f)
            if num_col:
                conn.execute(f"UPDATE raw_salary SET {num_col}=? WHERE id=?",
                             (_parse_num(n), rid))
        ctx = dict(
            friendly_table=build_friendly_table("raw_salary", old.get("sheet_name") or ""),
            invoice_no="",
            buyer=(old.get("staff_name") or "").strip(),
            amount=(old.get("net_raw") or "").strip(),
            handlers="",
        )
        log_change(conn, "raw_salary", str(rid), "edit", "", "", note, **ctx)
        for f, o, n in changes:
            log_change(conn, "raw_salary", str(rid), f, o, n, note, **ctx)
        conn.commit()
    finally:
        conn.close()
