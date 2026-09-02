"""导入复核 — 导入前留痕（确认入库后补录人工干预，spec §12.6 / §14）

导入确认页的人工干预分两类，在「确认入库」写库成功后统一落 change_log：
- fix（修正问题行，凭空填）：1 条 field='(导入修正)'，old=''，new=摘要
- edit（编辑已解析行）：逐字段 field=列名 old→new，备注/收款有变补摘要行

两类均：friendly_table='发票台账 · 导入修正'（修改记录页「修改表名」列可见
"来源"）、note 记性质（导入前修正无原因输入框）、镜表行 synced=0。

独立事务：留痕失败不回滚已成功入库的导入，由调用方决定提示与否。
"""
from __future__ import annotations

from typing import Dict, List

from app.db import get_conn
from app.engine.change_log import log_change

FIX_TABLE = "发票台账 · 导入修正"

# edit 逐字段映射（code -> 展示标签）；值取自 invoice 解析结构
_FIELD_DIFFS = [
    ("invoice_date", "开票日期"),
    ("total_amount", "金额"),
    ("buyer", "对方"),
    ("case_no", "案号"),
    ("handler_text", "经办人"),
]


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _receipt_text(split) -> str:
    if not split:
        return ""
    return "、".join(f"{name} {amt:g}({ym})" for name, amt, ym in split)


def _fix_summary(new: Dict) -> str:
    """fix 条目的新值摘要（问题行原无完整解析值）。"""
    parts = []
    if new.get("invoice_date"):
        parts.append(f"开票日期 {new['invoice_date']}")
    if new.get("total_amount"):
        parts.append(f"金额 {new['total_amount']:g}")
    if new.get("handler_text"):
        parts.append(f"经办人 {new['handler_text']}")
    if new.get("split_receipts"):
        parts.append("收款 " + _receipt_text(new["split_receipts"]))
    if new.get("buyer"):
        parts.append(f"对方 {new['buyer']}")
    return "；".join(parts) or "（空）"


def _field_rows(item: Dict) -> List[tuple]:
    """edit 条目 → [(field, old, new), ...]（仅真实变化）。"""
    old, new = item.get("old") or {}, item.get("new") or {}
    rows = []
    for code, _label in _FIELD_DIFFS:
        if code not in new:
            continue
        o, n = _fmt(old.get(code)), _fmt(new.get(code))
        if o != n:
            rows.append((code, o, n))
    # 收款明细（split_receipts 显式接管后，备注推导已被清空，old 侧从原备注无法直接还原，
    # 故只记 new 侧摘要；原侧标「(按原备注)」）
    if "split_receipts" in new:
        n_txt = _receipt_text(new.get("split_receipts")) or "（清空收款）"
        if n_txt != "（清空收款）" or "split_receipts" in old:
            rows.append(("收款明细", "(按原备注)", n_txt))
    return rows


def log_import_fixes(batch_id: int, items: List[Dict]) -> Dict:
    """确认入库后补录导入前人工干预（独立事务）。

    items 结构见 UnifiedImportDialog.collect_import_fixes。
    返回 {"logged": n, "skipped": m}；查不到镜表行（罕见）记 skipped。
    """
    logged = skipped = 0
    if not items:
        return {"logged": 0, "skipped": 0}
    conn = get_conn()
    try:
        for it in items:
            no = it.get("invoice_no") or ""
            if not no:
                skipped += 1
                continue
            row = conn.execute(
                "SELECT id, buyer, amount_raw, handler_text, sheet_name, invoice_no "
                "FROM raw_ledger WHERE import_batch_id=? AND invoice_no=? "
                "ORDER BY id DESC LIMIT 1",
                (batch_id, no),
            ).fetchone()
            if row is None:
                skipped += 1
                continue
            rid = row["id"]
            old = it.get("old") or {}
            ctx = dict(
                friendly_table=FIX_TABLE,
                invoice_no=no,
                buyer=str(old.get("buyer") or row["buyer"] or ""),
                amount=str(old.get("total_amount")
                           or row["amount_raw"] or ""),
                handlers=str(old.get("handler_text")
                             or row["handler_text"] or ""),
            )
            note = "导入时人工修正" if it.get("kind") == "fix" else "导入时人工编辑"
            if it.get("kind") == "fix":
                log_change(conn, "raw_ledger", str(rid), "(导入修正)", "",
                           _fix_summary(it.get("new") or {}), note, **ctx)
            else:
                field_rows = _field_rows(it)
                if not field_rows:
                    skipped += 1
                    continue
                for code, o, n in field_rows:
                    log_change(conn, "raw_ledger", str(rid), code, o, n, note, **ctx)
            # 与 Excel 原件不一致/无原件（导入期修正编辑均属人工干预）
            conn.execute("UPDATE raw_ledger SET synced=0 WHERE id=?", (rid,))
            logged += 1
        conn.commit()
    finally:
        conn.close()
    return {"logged": logged, "skipped": skipped}
