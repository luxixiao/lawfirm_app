"""导入复核 — 导入后回写引擎（单事务，spec §5/§12/§13.3）

apply_edit：改 raw_ledger 镜表行（复用 update_row 的逐字段审计 L1 与
invoice/charge_detail 同步）→ 涉及收款口径时经 _write_collection_for_invoice
删+重写 collection（含 received_snapshot 快照）→ synced=0 → 补 L2「(同步)」
汇总审计。任一步失败整体回滚。

范围：仅发票行（kind='invoice'）。预收款行不需要回写/确认/编辑（用户明确），
不进入本引擎（比对页本就不展示预收款行）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.db import get_conn
from app.engine import raw_ledger as rl
from app.engine.change_log import build_friendly_table, log_change
from app.importer.importer import _write_collection_for_invoice
from app.importer.parse_handler import parse_handler_column
from app.importer.parse_remark import parse_remark

_FIELD_LABELS = {
    "seq": "序号", "invoice_date_raw": "开票日期", "invoice_no": "发票号码",
    "buyer": "对方", "amount_raw": "金额", "handler_text": "经办人",
    "remark": "备注", "case_no": "案号", "recv_date_raw": "收到日期",
}


def _parse_total(txt) -> float:
    return float(str(txt or "").replace(",", "").replace("，", "")) if txt else 0.0


def _validated_handlers(text: str, total: float, invoice_no: str) -> None:
    """经办人文本须可解析（合计=总额）。解析失败抛 ValueError（触发整体回滚）。"""
    if not (text or "").strip():
        return
    try:
        parse_handler_column(text, total, invoice_no)
    except Exception as e:  # noqa: BLE001 -> 统一转 ValueError 由调用方提示
        raise ValueError(f"经办人列无法解析（合计须=发票总额）：{e}") from e


def _rebuild_inv(row: Dict, period: str, receipts: Optional[List[tuple]] = None) -> Dict:
    """按编辑后的镜表行重建结构化发票（供 _write_collection_for_invoice）。"""
    total = _parse_total(row.get("amount_raw"))
    no = (row.get("invoice_no") or "").strip()
    try:
        year = int(str(period)[:4])
    except Exception:  # noqa: BLE001
        year = None
    try:
        rem = parse_remark(row.get("remark"), default_year=year)
    except Exception:  # noqa: BLE001
        rem = {}
    inv: Dict = {
        "invoice_no": no,
        "total_amount": total,
        "is_red": total < 0,
        "remark": rem,
        "sheet_name": row.get("sheet_name") or "",
        "row_no": row.get("row_no") or 0,
    }
    if receipts is not None:
        inv["split_receipts"] = receipts  # 逐经办人收款显式覆盖（含空列表=清空收款）
    return inv


def apply_edit(period: str, raw_id: int, patch: Dict, note: str,
               rewrite_collection: bool = False) -> Dict:
    """导入后回写（单事务）。patch 键 = EDIT_FIELDS 子集（原始文本语义）+ receipts。

    receipts: Optional[List[(person_name, amount, ym)]] —— 显式收款明细覆盖；
    None = 不动收款；[] = 清空收款。
    rewrite_collection=True：即使备注未变化也按当前备注重写收款（供「还原为
    原件」撤销早前的显式收款覆盖，见 restore_from_archive）。
    返回 {"raw_id", "changed": [字段...], "summary": str}。
    """
    if not (note or "").strip():
        raise ValueError("请填写修改原因（必填）")

    conn = get_conn()
    try:
        old = rl.get_row(raw_id, conn)
        if old is None:
            raise ValueError(f"镜表行不存在（id={raw_id}）")
        if (old.get("kind") or "invoice") != "invoice":
            raise ValueError("仅发票行支持回写；预收款数据请到「业务数据 → 预收款」页处理")

        # ---- 合并 patch（仅认 EDIT_FIELDS 键）----
        data = {f: old.get(f) or "" for f in rl.EDIT_FIELDS}
        receipts = patch.get("receipts")
        if not isinstance(receipts, list):
            receipts = None
        for k, v in patch.items():
            if k in rl.EDIT_FIELDS and v is not None:
                data[k] = str(v)

        # ---- 预校验（在改动前拦截，避免半途回滚）----
        new_no = data["invoice_no"].strip()
        old_no = (old.get("invoice_no") or "").strip()
        if new_no != old_no:
            clash = conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?",
                                 (new_no,)).fetchone()
            if clash:
                raise ValueError(f"目标发票号 {new_no} 已存在，无法改号")
        # 经办人文本「真的变了」才校验合计=总额（纯改金额时允许保留原分摊，
        # 与旧发票台账页编辑口径一致：分摊差异会在复核页如实展示，供下次修正）
        if data["handler_text"] != (old.get("handler_text") or ""):
            _validated_handlers(data["handler_text"],
                                _parse_total(data["amount_raw"]), new_no)
        # 金额文本须可严格解析（raw_ledger._parse_total 对非法文本宽松返回 0.0，
        # 会造成静默清零，这里前置拦截）
        if (data["amount_raw"] or "").strip():
            try:
                _parse_total(data["amount_raw"])
            except ValueError:
                raise ValueError(f"金额无法解析：{data['amount_raw']!r}") from None

        # ---- 1) 镜表 + L1 审计 + invoice/charge_detail 同步（复用 update_row）----
        changed = rl.update_row(raw_id, data, note=note, conn=conn)

        # ---- 2) 收款重写（备注变了 / 显式 receipts / 强制重写）----
        # 纯改金额/经办人/购方不触发——既有 collection 由旧备注推导，未变则保留，
        # 避免"新备注为空 → 重写清空既有显式收款"的数据丢失。
        coll_rewritten = False
        coll_n = 0
        if receipts is not None or "remark" in changed or rewrite_collection:
            fresh = rl.get_row(raw_id, conn) or data
            fresh["amount_raw"] = data["amount_raw"]
            fresh["remark"] = data["remark"]
            fresh["invoice_no"] = new_no or fresh["invoice_no"]
            inv = _rebuild_inv(fresh, period, receipts)
            batch_id = old.get("import_batch_id") or 0
            msg = _write_collection_for_invoice(conn, inv, batch_id)
            if msg:
                raise ValueError(msg)
            if receipts is not None:
                coll_n = len(receipts)
            else:
                coll_n = conn.execute(
                    "SELECT count(*) FROM collection WHERE invoice_no=? AND source='import'",
                    (new_no,)).fetchone()[0]
            coll_rewritten = True

        # ---- 3) 修订标记：改过即与 Excel 原件不一致 ----
        conn.execute("UPDATE raw_ledger SET synced=0 WHERE id=?", (raw_id,))

        # ---- 4) L2 同步汇总（1 条）----
        ctx = dict(
            friendly_table=build_friendly_table("raw_ledger", old.get("sheet_name") or ""),
            invoice_no=old_no,
            buyer=(old.get("buyer") or "").strip(),
            amount=(old.get("amount_raw") or "").strip(),
            handlers=(old.get("handler_text") or "").strip(),
        )
        labels = [_FIELD_LABELS.get(f, f) for f in changed]
        parts = []
        if labels:
            parts.append("字段变更：" + "、".join(labels))
        if coll_rewritten:
            parts.append(f"collection 重写 {coll_n} 条（含 received_snapshot）")
        parts.append("synced=0（与 Excel 原件不一致，可「还原为原件」）")
        log_change(conn, "raw_ledger", str(raw_id), "(同步)", "", "；".join(parts),
                   note, **ctx)

        conn.commit()
        return {"raw_id": raw_id, "changed": changed,
                "summary": f"已回写：{'、'.join(labels) if labels else '仅收款'}；修改原因已留痕"}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def restore_from_archive(period: str, raw_id: int, archive_path: str) -> Dict:
    """单行还原为 Excel 原件（spec §5.3）。

    存档原件从未被修改 → 对存档文件重解析，按 (sheet_name, row_no) 匹配该行，
    用导入期同款 _raw_cell 取数重建原始镜表文本 → 走 apply_edit 覆盖并同步
    业务表（rewrite_collection=True：无条件按原备注重写收款，撤销早前的显式
    收款覆盖）→ synced 置回 1。
    """
    from app.importer.importer import _raw_cell, parse_ledger_file

    conn = get_conn()
    try:
        row = rl.get_row(raw_id, conn)
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"镜表行不存在（id={raw_id}）")
    if (row.get("kind") or "invoice") != "invoice":
        raise ValueError("仅发票行支持还原；预收款数据请到「业务数据 → 预收款」页处理")
    sheet_name = row.get("sheet_name") or ""
    try:
        row_no = int(row.get("row_no") or 0)
    except (TypeError, ValueError):
        row_no = 0
    if not sheet_name or row_no <= 0:
        raise ValueError("该行无原件坐标（多为导入时人工修正的问题行，非源台账行），无法还原")
    if not archive_path:
        raise ValueError("该账期无存档文件，无法还原原件")

    parsed = parse_ledger_file(str(archive_path), period)
    cand = None
    for inv in parsed.get("invoices", []):
        try:
            same = (str(inv.get("sheet_name") or "") == str(sheet_name)
                    and int(inv.get("row_no") or 0) == row_no)
        except (TypeError, ValueError):
            same = False
        if same:
            cand = inv
            break
    if cand is None:
        raise ValueError(f"存档中 {sheet_name} 第 {row_no} 行无解析结果"
                         "（该行在导入时被记为问题行，无原件可还原）")

    rr, hd = cand.get("raw_row"), cand.get("header")
    remark_raw = cand.get("remark_raw")
    patch = {
        "seq": _raw_cell(rr, hd, "序号"),
        "invoice_date_raw": _raw_cell(rr, hd, "开票日期", "开具日期"),
        "invoice_no": cand.get("invoice_no") or "",
        "buyer": cand.get("buyer") or "",
        "amount_raw": _raw_cell(rr, hd, "金额", "开票金额"),
        "case_no": cand.get("case_no") or "",
        "remark": remark_raw if remark_raw is not None else (cand.get("remark") or ""),
        "handler_text": cand.get("handler_text") or "",
    }
    res = apply_edit(period, raw_id, patch, note="还原为 Excel 原件",
                     rewrite_collection=True)
    conn = get_conn()
    try:
        conn.execute("UPDATE raw_ledger SET synced=1 WHERE id=?", (raw_id,))
        conn.commit()
    finally:
        conn.close()
    return {**res, "restored": True,
            "summary": "已还原为 Excel 原件（收款按原备注重写）"}
