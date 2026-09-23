"""发票台账原始镜表引擎（方案 D）

- raw_invoice 是 Excel 逐行 1:1 镜像（数值列原样存文本），与归一化 invoice 表解耦。
- 本模块提供：列表/查询、增、改、删（级联删 invoice）、归一化到 invoice、批量同步。
- 修改记录：所有对发票台账的改动（raw_invoice 行编辑、同步）写入 change_log，
  满足「修改记录只记录发票台账」（2.5）。
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

from app.db import get_conn
from app.importer.date_utils import normalize_date
from app.engine.change_log import log_change

_RED_RE = re.compile(r"被红冲蓝字数电票号码[:：]\s*(\d+)")

RAW_FIELDS = [
    "sheet_name", "row_no", "seq", "invoice_no", "kind", "invoice_date_raw",
    "status", "voucher_no", "buyer", "total_amount_raw", "net_amount_raw",
    "tax_rate_raw", "tax_raw", "goods", "remark",
]


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def distinct_sheets(imported_only: bool = False) -> List[str]:
    conn = get_conn()
    try:
        clauses = ["sheet_name != ''"]
        if imported_only:
            clauses.append("import_batch_id IS NOT NULL")
        where = " WHERE " + " AND ".join(clauses)
        return [r["sheet_name"] for r in conn.execute(
            f"SELECT DISTINCT sheet_name FROM raw_invoice{where} ORDER BY sheet_name")]
    finally:
        conn.close()


def list_raw(sheet: str = "", status: str = "", keyword: str = "", imported_only: bool = False) -> List[Dict]:
    """返回 raw_invoice 行（按 sheet/row 排序）。

    status:
      ""      = 全部
      "red"   = 仅红字（价税合计为负）
      "pending"= 仅待同步（synced=0）
    keyword 匹配 发票号码/购方/备注（LIKE）。
    imported_only=True 时仅返回来自销项导入的行（import_batch_id 非空），
    排除历史手动增改的残留行。
    """
    clauses, params = [], []
    if sheet:
        clauses.append("sheet_name = ?")
        params.append(sheet)
    if status == "pending":
        clauses.append("synced = 0")
    if imported_only:
        clauses.append("import_batch_id IS NOT NULL")
    if keyword:
        clauses.append("(invoice_no LIKE ? OR buyer LIKE ? OR remark LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM raw_invoice{where} ORDER BY sheet_name, row_no", params).fetchall()
    finally:
        conn.close()
    out = [dict(r) for r in rows]
    if status == "red":
        out = [r for r in out if _parse_total(r.get("total_amount_raw")) < 0]
    return out


def get_row(rid: int) -> Optional[Dict]:
    conn = get_conn()
    try:
        r = conn.execute("SELECT * FROM raw_invoice WHERE id=?", (rid,)).fetchone()
    finally:
        conn.close()
    return dict(r) if r else None


def count_pending() -> int:
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM raw_invoice WHERE synced=0").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 写：增 / 改 / 删
# ---------------------------------------------------------------------------
def insert_row(data: Dict, note: str = "") -> int:
    cols = ", ".join(RAW_FIELDS)
    placeholders = ", ".join(["?"] * len(RAW_FIELDS))
    vals = [data.get(f, "") or "" for f in RAW_FIELDS]
    conn = get_conn()
    try:
        cur = conn.execute(
            f"INSERT INTO raw_invoice ({cols}) VALUES ({placeholders})", vals)
        rid = cur.lastrowid
        log_change(conn, "raw_invoice", str(rid), "create", "",
                   data.get("invoice_no") or "(空)", f"新增原始行 {note}".strip())
        conn.commit()
    finally:
        conn.close()
    return rid


def update_row(rid: int, data: Dict, note: str = "") -> None:
    old = get_row(rid)
    if old is None:
        return
    changes = []
    for f in RAW_FIELDS:
        o, n = old.get(f) or "", data.get(f) or ""
        if str(o) != str(n):
            changes.append((f, o, n))
    conn = get_conn()
    try:
        sets = ", ".join(f"{f}=?" for f in RAW_FIELDS)
        vals = [data.get(f) or "" for f in RAW_FIELDS]
        vals.append(rid)
        conn.execute(f"UPDATE raw_invoice SET {sets} WHERE id=?", vals)
        if changes:
            log_change(conn, "raw_invoice", str(rid), "edit", "", "", note)
            for f, o, n in changes:
                log_change(conn, "raw_invoice", str(rid), f, o, n, note)
        conn.commit()
    finally:
        conn.close()


def delete_rows(ids: Iterable[int], note: str = "") -> int:
    ids = list(ids)
    if not ids:
        return 0
    conn = get_conn()
    try:
        n = 0
        for rid in ids:
            row = conn.execute(
                "SELECT invoice_no, synced, import_batch_id FROM raw_invoice WHERE id=?", (rid,)).fetchone()
            if row is None:
                continue
            inv_no = row["invoice_no"]
            ibid = row["import_batch_id"]
            # 级联：若该原始行已同步过（invoice 中存在此发票号），同时删除 invoice 及关联数据
            if inv_no and conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (inv_no,)).fetchone():
                # charge_detail / collection 只删本批 import 数据，保护 manual 与他批
                # import 收款（P0-3 同类根因：unscoped DELETE 会抹历史）。
                if ibid is not None:
                    conn.execute("DELETE FROM charge_detail WHERE invoice_no=? AND import_batch_id=?", (inv_no, ibid))
                    conn.execute(
                        "DELETE FROM collection WHERE invoice_no=? AND source='import' AND import_batch_id=?",
                        (inv_no, ibid))
                else:
                    # 该 raw 行无 import_batch_id（手动建）：charge_detail 按 NULL 批限定，
                    # collection 至少限定 source='import' 保护 manual 收款
                    conn.execute("DELETE FROM charge_detail WHERE invoice_no=? AND import_batch_id IS NULL", (inv_no,))
                    conn.execute("DELETE FROM collection WHERE invoice_no=? AND source='import'", (inv_no,))
                conn.execute("DELETE FROM invoice WHERE invoice_no=?", (inv_no,))
                log_change(conn, "invoice", inv_no, "delete", inv_no, "",
                           f"删除发票台账原始行 #{rid} 级联删除 {note}".strip())
            conn.execute("DELETE FROM raw_invoice WHERE id=?", (rid,))
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 归一化 + 同步
# ---------------------------------------------------------------------------
def _parse_total(txt: Optional[str]) -> float:
    if not txt:
        return 0.0
    try:
        return float(str(txt).replace(",", ""))
    except ValueError:
        return 0.0


def normalize_to_invoice(raw: Dict, default_year: int | None = None) -> Dict:
    """把一行 raw_invoice 映射为 invoice 归一化字段（不含 charge_detail）"""
    total = _parse_total(raw.get("total_amount_raw"))
    net = _parse_total(raw.get("net_amount_raw"))
    tax = _parse_total(raw.get("tax_raw"))
    year = default_year or __import__("datetime").datetime.now().year
    remark = raw.get("remark") or ""
    m = _RED_RE.search(remark)
    return {
        "invoice_no": (raw.get("invoice_no") or "").strip(),
        "invoice_date": normalize_date(raw.get("invoice_date_raw") or "", default_year=year),
        "buyer": raw.get("buyer") or "",
        "total_amount": total,
        "kind": raw.get("kind") or "",
        "status": raw.get("status") or "",
        "voucher_no": raw.get("voucher_no") or "",
        "goods": raw.get("goods") or "",
        "net_amount": net if raw.get("net_amount_raw") else None,
        "tax_rate": raw.get("tax_rate_raw") or "",
        "tax": tax if raw.get("tax_raw") else None,
        "orig_invoice_no": m.group(1) if m else "",
        "remark": remark,
        "is_red": total < 0,
    }


def sync_to_invoice(ids: Optional[List[int]] = None, note: str = "从发票台账同步") -> Dict:
    """把 raw_invoice 行同步到归一化 invoice 表。

    - 仅更新 invoice 基础字段，保留 charge_detail（经办人）不受影响。
    - 已同步的行置 synced=1；无发票号码的行跳过。
    返回 {synced, skipped}。
    """
    conn = get_conn()
    try:
        if ids:
            placeholders = ", ".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT * FROM raw_invoice WHERE id IN ({placeholders}) ORDER BY sheet_name, row_no",
                ids).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM raw_invoice ORDER BY sheet_name, row_no").fetchall()
        synced, skipped = 0, 0
        for r in rows:
            inv = normalize_to_invoice(dict(r))
            if not inv["invoice_no"]:
                skipped += 1
                continue
            existing = conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?",
                                   (inv["invoice_no"],)).fetchone()
            if existing:
                conn.execute(
                    """UPDATE invoice SET invoice_date=?, buyer=?, total_amount=?, kind=?, status=?,
                       voucher_no=?, goods=?, net_amount=?, tax_rate=?, tax=?, orig_invoice_no=?, remark=?
                       WHERE invoice_no=?""",
                    (inv["invoice_date"], inv["buyer"], inv["total_amount"], inv["kind"], inv["status"],
                     inv["voucher_no"], inv["goods"], inv["net_amount"], inv["tax_rate"], inv["tax"],
                     inv["orig_invoice_no"], inv["remark"], inv["invoice_no"]),
                )
            else:
                conn.execute(
                    """INSERT INTO invoice
                       (invoice_no, invoice_date, buyer, total_amount, kind, status, voucher_no, goods,
                        net_amount, tax_rate, tax, orig_invoice_no, remark, source)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'sync')""",
                    (inv["invoice_no"], inv["invoice_date"], inv["buyer"], inv["total_amount"], inv["kind"],
                     inv["status"], inv["voucher_no"], inv["goods"], inv["net_amount"], inv["tax_rate"],
                     inv["tax"], inv["orig_invoice_no"], inv["remark"]),
                )
            conn.execute("UPDATE raw_invoice SET synced=1 WHERE id=?", (r["id"],))
            synced += 1
        log_change(conn, "invoice", "SYNC", "sync", "", f"{synced} 行",
                   f"{note}（同步 {synced} 行，跳过 {skipped} 行无发票号）")
        conn.commit()
        return {"synced": synced, "skipped": skipped}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
