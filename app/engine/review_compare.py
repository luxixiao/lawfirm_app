"""导入复核 — 导入后模式比对引擎（镜表 ↔ 业务表，融合三维度）

比对基准 = `raw_ledger` 镜表（最新 active 批次，spec §1.4），业务侧 =
invoice / charge_detail / collection（received_snapshot 优先，缺快照批次
回退按账期窗口还原 collection）。

每行 = 一张发票（按 invoice_no 归并镜表与库两侧，镜表内同号取末行——
与导入"后者覆盖前者"一致），一次比对全部维度：金额 / 经办人分摊 / 已收认定。
状态列取值：一致 / 不符 / 仅源有 / 仅库有 / 已确认异常（spec §4）。

纯逻辑模块，不依赖 Qt；测试用内存库注入 get_conn。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Dict, List, Optional, Tuple

from app.db import get_conn
from app.importer.importer import compute_expected_receipts
from app.importer.parse_handler import parse_handler_column
from app.importer.parse_remark import parse_remark

# 差异判定容差（沿用旧导入校验页口径）
_AMT_EPS = 0.005
_SUM_EPS = 0.01
_RECV_EPS = 0.01


def _money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else str(v or "")


def _ht(handlers: Dict[str, float]) -> str:
    if not handlers:
        return "—"
    return "、".join(f"{n} {_money(a)}" for n, a in sorted(handlers.items()))


def _names_only(handler_text: str) -> bool:
    """源经办人列为纯人名（无金额）→ 解析时按均分，属「沿用首月拆分」的
    省略写法，不应判为与库不一致（库内保留的是最初的具体拆分）。"""
    return not any(ch.isdigit() for ch in (handler_text or ""))


def latest_batch(period: str) -> Optional[sqlite3.Row]:
    """该账期最新一笔 active 发票台账批次（同账期多批次以最新为准）。"""
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT id, file_name, archive_path FROM import_batch "
            "WHERE batch_type='ledger' AND period=? AND status='active' "
            "ORDER BY imported_at DESC LIMIT 1",
            (period,),
        ).fetchone()
    finally:
        conn.close()


def _derive_raw(r: sqlite3.Row, period: str) -> Dict:
    """raw_ledger 行 → 结构化源发票（复用导入期同一套解析器）。"""
    total = r["amount_num"] or 0.0
    no = r["invoice_no"] or ""
    try:
        handlers = parse_handler_column(r["handler_text"] or "", total, no)
    except Exception:  # noqa: BLE001 解析失败按空分摊（与导入期失败进问题行的口径一致）
        handlers = []
    try:
        year = int(str(period)[:4])
    except Exception:  # noqa: BLE001
        year = None
    try:
        rem = parse_remark(r["remark"], default_year=year)
    except Exception:  # noqa: BLE001
        rem = {}
    return {
        "raw_id": r["id"],
        "invoice_no": no,
        "buyer": r["buyer"] or "",
        "total_amount": total,
        "is_red": total < 0,
        "handler_text": r["handler_text"] or "",
        "handlers": {n: a for n, a in handlers},
        "remark": rem,
        "source": f"{r['sheet_name'] or r['sheet_key'] or '—'} · 第{r['row_no'] or 0}行",
    }


def _raw_side(batch_id: int, period: str) -> List[Dict]:
    """镜表行 → 结构化源侧（保持镜表行序；同号取末行）。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, sheet_key, sheet_name, row_no, invoice_no, buyer, "
            "amount_num, handler_text, remark FROM raw_ledger "
            "WHERE import_batch_id=? AND kind='invoice' ORDER BY id",
            (batch_id,),
        ).fetchall()
    finally:
        conn.close()
    out: Dict[str, Dict] = {}
    order: List[str] = []
    for r in rows:
        no = r["invoice_no"] or ""
        if no not in out:
            order.append(no)
        out[no] = _derive_raw(r, period)  # 同号后者覆盖（与导入语义一致）
    return [out[no] for no in order]


def build_review_rows(period: str) -> Tuple[Optional[Dict], List[Dict]]:
    """融合比对主入口。返回 (batch, rows)；无批次时 batch=None、rows=[]。"""
    batch = latest_batch(period)
    if batch is None:
        return None, []
    batch_id = batch["id"]
    src_list = _raw_side(batch_id, period)
    src_map = {s["invoice_no"]: s for s in src_list}
    inv_nos = list(src_map)

    conn = get_conn()
    try:
        # ---- 库侧 invoice / charge_detail ----
        # 范围 = 本批次落库（import_batch_id）∪ 镜表同号（防历史批次缺 batch_id 漏配）
        db_inv: Dict[str, Dict] = {}
        db_cd: Dict[str, Dict[str, float]] = {}
        cond = "import_batch_id=?"
        params: list = [batch_id]
        if inv_nos:
            ph = ",".join("?" * len(inv_nos))
            cond += f" OR invoice_no IN ({ph})"
            params += inv_nos
        for r in conn.execute(
            f"SELECT invoice_no, buyer, total_amount FROM invoice "
            f"WHERE source='import' AND ({cond})",
            params,
        ):
            db_inv[r["invoice_no"]] = {
                "buyer": r["buyer"] or "",
                "total_amount": r["total_amount"] or 0.0,
            }
        for r in conn.execute(
            f"SELECT invoice_no, person_name, billing_amount FROM charge_detail "
            f"WHERE source='import' AND ({cond})",
            params,
        ):
            db_cd.setdefault(r["invoice_no"], {})[r["person_name"]] = r["billing_amount"]

        # ---- 已收：快照优先（本批次 期望/实际），缺快照回退账期窗口还原 ----
        snap: Dict[str, Tuple[Dict, Dict]] = {}
        for r in conn.execute(
            "SELECT invoice_no, expected_json, actual_json FROM received_snapshot "
            "WHERE import_batch_id=?",
            (batch_id,),
        ):
            try:
                snap[r["invoice_no"]] = (json.loads(r["expected_json"]),
                                         json.loads(r["actual_json"]))
            except Exception:  # noqa: BLE001
                continue
        live_act: Dict[str, List[Tuple[str, float, str]]] = {}
        for r in conn.execute(
            "SELECT invoice_no, receipt_date, amount, person_name FROM collection "
            "WHERE source='import' AND substr(receipt_date,1,7) <= ?",
            (period,),
        ):
            live_act.setdefault(r["invoice_no"], []).append(
                ((r["receipt_date"] or "")[:7], r["amount"], r["person_name"] or ""))
    finally:
        conn.close()

    notes = load_confirmed_notes(period)

    rows: List[Dict] = []
    for no in list(src_map) + sorted(set(db_inv) - set(src_map)):
        s = src_map.get(no)
        d_inv = db_inv.get(no)
        d_handlers = db_cd.get(no, {})

        # ---- 源侧已收（声称）----
        exp_total, exp_items = 0.0, []
        if s is not None:
            if no in snap:
                exp = snap[no][0]
                exp_total = float(exp.get("total", 0.0) or 0.0)
                exp_items = exp.get("items", []) or []
            else:
                exp_total, exp_items = compute_expected_receipts(s)
        recv_src = "、".join(f"{it['ym']} {it['amount']:,.2f}" for it in exp_items) \
            or (("红字无收款" if s and s.get("is_red") else "未收款") if s else "—")

        # ---- 库侧已收（实际落库）----
        if no in snap:
            act = snap[no][1]
            act_items = act.get("items", []) or []
            act_total = float(act.get("total", 0.0) or 0.0)
        else:
            items = live_act.get(no, [])
            act_total = sum(a for _, a, _ in items)
            act_items = [{"ym": ym, "amount": a} for ym, a, _ in items]
        recv_db = "、".join(f"{it['ym']} {it['amount']:,.2f}" for it in act_items) or "—"

        # ---- 逐维度差异 ----
        flags: List[str] = []
        if s is None:
            status = "仅库有"
            flags = ["镜表无此行"]
        elif d_inv is None:
            status = "仅源有"
            flags = ["库中缺失"]
        else:
            if abs((s["total_amount"] or 0.0) - (d_inv["total_amount"] or 0.0)) > _AMT_EPS:
                flags.append("金额不一致")
            # 经办人分摊（保留「纯人名均分沿用首月拆分」豁免）
            p_h, d_h = s["handlers"], d_handlers
            if set(p_h) != set(d_h):
                miss = set(p_h) - set(d_h)
                extra = set(d_h) - set(p_h)
                if miss:
                    flags.append("经办人缺失:" + "、".join(sorted(miss)))
                if extra:
                    flags.append("经办人多出:" + "、".join(sorted(extra)))
            else:
                diff = [n for n in p_h if abs(p_h[n] - d_h.get(n, 0.0)) > _AMT_EPS]
                if diff and not (_names_only(s["handler_text"])
                                 and set(p_h) == set(d_h)):
                    flags.append("分摊金额不符:" + "、".join(sorted(diff)))
                elif abs(sum(p_h.values()) - sum(d_h.values())) > _SUM_EPS:
                    flags.append("分摊合计不符")
            # 已收认定
            src_warn = ""
            rem = s.get("remark") or {}
            if rem.get("remaining") is not None and rem.get("receipts") \
                    and not rem.get("pure_date"):
                if abs((exp_total + rem["remaining"]) - s["total_amount"]) > _SUM_EPS:
                    src_warn = "源勾稽不平；"
            if abs(exp_total - act_total) > _RECV_EPS:
                flags.append(f"{src_warn}已收认定不符(差{exp_total - act_total:,.2f})")
            elif src_warn:
                flags.append(src_warn.rstrip("；"))
            status = "不符" if flags else "一致"

        note = notes.get(no, "")
        if status in ("不符", "仅源有", "仅库有") and note:
            status = "已确认异常"

        rows.append({
            "invoice_no": no,
            "source": s["source"] if s else "—",
            "buyer_src": s["buyer"] if s else "—",
            "buyer_db": d_inv["buyer"] if d_inv else "—",
            "amount_src": s["total_amount"] if s else None,
            "amount_db": d_inv["total_amount"] if d_inv else None,
            "handlers_src": _ht(s["handlers"]) if s else "—",
            "handlers_db": _ht(d_handlers) if d_inv is not None else "—",
            "recv_src": recv_src,
            "recv_db": recv_db,
            "status": status,
            "flags": flags,
            "detail": "；".join(flags),
            "confirmed_note": note,
            "raw_id": s["raw_id"] if s else None,
            "src": s,
        })
    return {"id": batch_id, "file_name": batch["file_name"],
            "archive_path": batch["archive_path"] or ""}, rows


def load_confirmed_notes(period: str) -> Dict[str, str]:
    """已确认异常备注：优先 dim='merged'（融合后新写入），缺则回退聚合旧
    handler/received 维度（历史数据兼容，spec §6.1）。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT dim, invoice_no, note FROM anomaly_note WHERE period=?",
            (period,),
        ).fetchall()
    finally:
        conn.close()
    merged: Dict[str, str] = {}
    legacy: Dict[str, List[str]] = {}
    for r in rows:
        if r["dim"] == "merged":
            merged[r["invoice_no"]] = r["note"]
        elif r["dim"] in ("handler", "received"):
            legacy.setdefault(r["invoice_no"], []).append(r["note"])
    out = dict(merged)
    for no, parts in legacy.items():
        if no not in out:
            out[no] = "；".join(parts)
    return out
