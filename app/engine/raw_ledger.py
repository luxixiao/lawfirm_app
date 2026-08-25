"""发票台账原始镜表引擎

- raw_ledger 是发票台账文档 Excel 逐 sheet 逐行 1:1 镜像（覆盖 sheet1~4 全部原始列），
  与归一化 invoice 表解耦。导入双写落库，可手工编辑。
- 修改记录：所有对 raw_ledger 的编辑写入 change_log（统一审计中心数据源之一）。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from app.db import get_conn
from app.engine.change_log import log_change

# 可编辑字段（编辑弹窗用）
EDIT_FIELDS = [
    "seq", "invoice_date_raw", "invoice_no", "buyer",
    "amount_raw", "handler_text", "remark", "case_no", "recv_date_raw",
]

# sheet 展示顺序
SHEET_ORDER = ["sheet1", "sheet2", "sheet3", "sheet4"]

# sheet 展示名（用于「发票台账」页的工作表筛选与来源列，避免显示 Excel 原始名
# 含账期前缀的怪名，如 "202502已开票已入账" / "2025已入账未开票"）
SHEET_LABELS = {
    "sheet1": "已开票已入账",
    "sheet2": "已开票未入账",
    "sheet3": "应收账款",
    "sheet4": "已入账未开票",
}


def sheet_label(key: str) -> str:
    """sheet_key -> 干净展示名；未知/空返回原值。"""
    return SHEET_LABELS.get(key, key or "")


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def sheet_keys() -> List[Dict]:
    """返回当前库中存在的 sheet（按固定顺序），含干净的展示名。

    展示名取自 SHEET_LABELS（已开票已入账等），不再使用 Excel 原始工作表名
    （原始名常带账期前缀且格式不统一，如 "202502已开票已入账"）。
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT sheet_key FROM raw_ledger "
            "WHERE import_batch_id IS NOT NULL AND sheet_key IS NOT NULL AND sheet_key != ''"
        ).fetchall()
    finally:
        conn.close()
    present = {r["sheet_key"] for r in rows}
    out = []
    for k in SHEET_ORDER:
        if k in present:
            out.append({"sheet_key": k, "sheet_name": SHEET_LABELS.get(k, k)})
    return out


def list_raw(sheet_key: str = "", keyword: str = "", status: str = "",
             imported_only: bool = False, with_period: bool = True) -> List[Dict]:
    """返回 raw_ledger 行（按 sheet/row 排序）。

    sheet_key: ""=全部；否则按 sheet1~4 过滤。
    status:    ""=全部；"red"=仅红字（金额<0）。
    keyword:   匹配 发票号码/对方/金额文本/案号（LIKE，不区分大小写）。
    imported_only: 仅来自导入的行（import_batch_id 非空）。
    with_period:  LEFT JOIN import_batch 带出 period（导入账期，如 "2025-01"），
                  用于「发票台账」页按台账时间（导入账期）筛选，而非按发票自身日期。
    """
    clauses, params = [], []
    if sheet_key:
        clauses.append("l.sheet_key = ?")
        params.append(sheet_key)
    if imported_only:
        clauses.append("l.import_batch_id IS NOT NULL")
    if keyword:
        clauses.append("(l.invoice_no LIKE ? OR l.buyer LIKE ? OR l.amount_raw LIKE ? OR l.case_no LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    join = " LEFT JOIN import_batch b ON b.id = l.import_batch_id" if with_period else ""
    sel = "l.*" + (", b.period AS period" if with_period else "")
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT {sel} FROM raw_ledger l{join}{where} ORDER BY l.sheet_key, l.row_no",
            params,
        ).fetchall()
    finally:
        conn.close()
    out = [dict(r) for r in rows]
    if status == "red":
        out = [r for r in out if _parse_total(r.get("amount_raw")) < 0]
    return out


def get_row(rid: int) -> Optional[Dict]:
    conn = get_conn()
    try:
        r = conn.execute("SELECT * FROM raw_ledger WHERE id=?", (rid,)).fetchone()
    finally:
        conn.close()
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# 写：编辑（手动修改能力，自动写审计）
# ---------------------------------------------------------------------------
def update_row(rid: int, data: Dict, note: str = "") -> None:
    """编辑一行 raw_ledger，逐字段比对并写 change_log（仅记录变了的字段）。"""
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
        conn.execute(f"UPDATE raw_ledger SET {sets} WHERE id=?", new_vals + [rid])
        # 金额文本变化同步更新数值列
        if "amount_raw" in (c[0] for c in changes):
            amt = data.get("amount_raw") or ""
            try:
                conn.execute("UPDATE raw_ledger SET amount_num=? WHERE id=?",
                             (_parse_total(amt), rid))
            except ValueError:
                pass
        log_change(conn, "raw_ledger", str(rid), "edit", "", "", note)
        for f, o, n in changes:
            log_change(conn, "raw_ledger", str(rid), f, o, n, note)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _parse_total(txt: Optional[str]) -> float:
    if not txt:
        return 0.0
    m = re.search(r"-?\d[\d,]*\.?\d*", str(txt))
    if not m:
        return 0.0
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return 0.0
