"""导入复核 — 导入后模式比对引擎（镜表 ↔ 业务表，融合三维度）

比对基准 = `raw_ledger` 镜表（最新 active 批次，spec §1.4），业务侧 =
invoice / charge_detail / collection（received_snapshot 优先，缺快照批次
回退按账期窗口还原 collection）。

每行 = 一张发票（按 invoice_no 归并镜表与库两侧，镜表内同号取末行——
与导入"后者覆盖前者"一致），一次比对全部维度：金额 / 经办人分摊 / 已收认定。
状态列取值：一致 / 不符 / 仅源有 / 仅库有 / 已确认异常（spec §4）。

期外票（sheet3 期外、本批次未建票）不另立状态，而是行上带 `needs_backfill`
标记：语义是「待补录待办」而非数据异常，由复核页决定呈现与计数。

库侧读取口径：invoice / charge_detail / collection 均取 `source IN ('import',
'manual')` —— 补录（manual）是期外票唯一的销账动作，若不纳入，补录完成后该行
会永远停在「库中缺失」，与「补录原票」页互相矛盾（那边票号对齐即移出）。

批 2（2026-09-18）：比对核心抽成 `lib_diff` / `_recv_sides` / `lib_recv_totals` /
`build_lib_context`，与 `import_confidence.evaluate`（**导入前**模式）共用同一口径
—— 导入复核统一化后，「台账 ⇄ 库」比对在**导入前**判进四态（命中 → 待确认）。
sheet3（应收账款）行不参与比对（应收视角 ≠ 发票视角，批 0 §9.6；实测排除后 0 差异）。

纯逻辑模块，不依赖 Qt；测试用内存库注入 get_conn。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Dict, List, Optional, Tuple

from app.db import get_conn
from app.engine import raw_ledger as rl
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


def _recv_sides(no: str, src: Optional[Dict], snap: Dict, live_act: Dict) -> Tuple[float, list, float, list]:
    """已收认定双方：源声称 `(exp_total, exp_items)` 与 库实际 `(act_total, act_items)`。

    批 2 抽出：`build_review_rows`（旧页）与 `import_confidence.evaluate`（导入前比对）
    必须同一口径。快照（本批次 期望/实际）优先；缺快照 → 源侧按
    `compute_expected_receipts` 还原、库侧按账期窗口还原 collection。
    `src=None`（仅库有行）→ 源侧恒 `(0.0, [])`（与既有行为一致）。
    """
    exp_total, exp_items = 0.0, []
    if src is not None:
        if no in snap:
            exp = snap[no][0]
            exp_total = float(exp.get("total", 0.0) or 0.0)
            exp_items = exp.get("items", []) or []
        else:
            exp_total, exp_items = compute_expected_receipts(src)
    if no in snap:
        act = snap[no][1]
        act_items = act.get("items", []) or []
        act_total = float(act.get("total", 0.0) or 0.0)
    else:
        items = live_act.get(no, [])
        act_total = sum(a for _, a, _ in items)
        act_items = [{"ym": ym, "amount": a} for ym, a, _ in items]
    return exp_total, exp_items, act_total, act_items


# 比对维度名（批 2：`lib_diff` 的 fields / `evaluate` 的原因列共用）
FIELD_AMOUNT = "金额"
FIELD_HANDLERS = "经办人分摊"
FIELD_RECV = "已收认定"


def lib_diff(src: Dict, d_inv: Dict, d_handlers: Dict[str, float],
             exp_total: float, act_total: float) -> Optional[Dict]:
    """源 ⇄ 库 **逐维度差异**核心（批 2 抽出，唯一口径）。

    比对三维：金额 / 经办人分摊 / 已收认定 —— 与 `build_review_rows` 逐字同一套规则
    （含「源经办人纯人名 → 豁免分摊金额比对」）。**前提：源、库两侧都有此票**
    （仅一侧有的情形由调用方按「仅源有 / 仅库有」单独处理）。

    一致返回 `None`；不一致返回::

        {"fields": ["金额", "经办人分摊"],      # 差异维度名（原因列短语用）
         "flags":  ["金额不一致", ...],          # 与旧行为完全相同的短语
         "detail": ["金额 台账3,400.00 ⇄ 库3,000.00", ...]}   # 逐维度对照值
    """
    flags: List[str] = []
    fields: List[str] = []

    def _hit(flag: str, field: str) -> None:
        flags.append(flag)
        if field not in fields:
            fields.append(field)

    # ---- 金额 ----
    if abs((src["total_amount"] or 0.0) - (d_inv["total_amount"] or 0.0)) > _AMT_EPS:
        _hit("金额不一致", FIELD_AMOUNT)
    # ---- 经办人分摊（保留「纯人名均分沿用首月拆分」豁免）----
    p_h, d_h = src["handlers"], d_handlers
    if set(p_h) != set(d_h):
        miss = set(p_h) - set(d_h)
        extra = set(d_h) - set(p_h)
        if miss:
            _hit("经办人缺失:" + "、".join(sorted(miss)), FIELD_HANDLERS)
        if extra:
            _hit("经办人多出:" + "、".join(sorted(extra)), FIELD_HANDLERS)
    else:
        diff = [n for n in p_h if abs(p_h[n] - d_h.get(n, 0.0)) > _AMT_EPS]
        if diff and not (_names_only(src["handler_text"]) and set(p_h) == set(d_h)):
            _hit("分摊金额不符:" + "、".join(sorted(diff)), FIELD_HANDLERS)
        elif abs(sum(p_h.values()) - sum(d_h.values())) > _SUM_EPS:
            _hit("分摊合计不符", FIELD_HANDLERS)
    # ---- 已收认定 ----
    src_warn = ""
    rem = src.get("remark") or {}
    if rem.get("remaining") is not None and rem.get("receipts") and not rem.get("pure_date"):
        if abs((exp_total + rem["remaining"]) - src["total_amount"]) > _SUM_EPS:
            src_warn = "源勾稽不平；"
    if abs(exp_total - act_total) > _RECV_EPS:
        _hit(f"{src_warn}已收认定不符(差{exp_total - act_total:,.2f})", FIELD_RECV)
    elif src_warn:
        _hit(src_warn.rstrip("；"), FIELD_RECV)

    if not flags:
        return None
    detail: List[str] = []
    if FIELD_AMOUNT in fields:
        detail.append(f"{FIELD_AMOUNT} 台账{_money(src['total_amount'])} "
                      f"⇄ 库{_money(d_inv['total_amount'])}")
    if FIELD_HANDLERS in fields:
        detail.append(f"{FIELD_HANDLERS} 台账{_ht(src['handlers'])} ⇄ 库{_ht(d_handlers)}")
    if FIELD_RECV in fields:
        detail.append(f"{FIELD_RECV} 台账{_money(exp_total)} ⇄ 库{_money(act_total)}")
    return {"fields": fields, "flags": flags, "detail": detail}


def lib_recv_totals(lib: Dict, no: str, src: Dict) -> Tuple[float, float]:
    """（批 2）某票的**已收认定**双方总额 → `(源声称, 库实际)`。

    `lib` = `build_lib_context` 的产物。快照优先；缺快照 → 源侧按
    `compute_expected_receipts` 还原、库侧按账期窗口还原 —— 与
    `build_review_rows` / `_recv_sides` 同一口径。
    """
    snap = lib.get("snap") or {}
    if no in snap:
        return (float(snap[no][0].get("total", 0.0) or 0.0),
                float(snap[no][1].get("total", 0.0) or 0.0))
    exp_total, _items = compute_expected_receipts(src)
    act_total = sum(a for _ym, a, _p in (lib.get("act") or {}).get(no, []))
    return exp_total, act_total


def build_lib_context(period: str, invoice_nos: Optional[List[str]] = None,
                      batch_id: Optional[int] = None, conn=None) -> Dict:
    """库侧三方（invoice / charge_detail / collection）一次读出 → 供逐行比对。

    批 2（导入前比对）：`import_confidence.evaluate` 拿它当「库」侧上下文，
    口径与 `build_review_rows` **完全一致** ——
    `source IN ('import','manual')`（补录 manual 是期外票唯一的销账动作）；
    `batch_id` 给出时快照优先，缺省（导入前还没有本批）只用 live collection。

    返回 `{"period", "inv", "cd", "act", "snap"}`；
    任一表读不出（旧库缺列等）→ 对应桶为空，比对按「库中缺失」处理。
    """
    own = conn is None
    if own:
        conn = get_conn()
    out: Dict = {"period": period, "inv": {}, "cd": {}, "act": {}, "snap": {}}
    try:
        cond, params = "1=1", []
        if invoice_nos:
            ph = ",".join("?" * len(invoice_nos))
            cond = f"invoice_no IN ({ph})"
            params = list(invoice_nos)
        for r in conn.execute(
            f"SELECT invoice_no, buyer, total_amount FROM invoice "
            f"WHERE source IN ('import','manual') AND ({cond})", params,
        ):
            out["inv"][r["invoice_no"]] = {
                "buyer": r["buyer"] or "",
                "total_amount": r["total_amount"] or 0.0,
            }
        for r in conn.execute(
            f"SELECT invoice_no, person_name, billing_amount FROM charge_detail "
            f"WHERE source IN ('import','manual') AND ({cond})", params,
        ):
            out["cd"].setdefault(r["invoice_no"], {})[r["person_name"]] = r["billing_amount"]
        if batch_id is not None:
            for r in conn.execute(
                "SELECT invoice_no, expected_json, actual_json FROM received_snapshot "
                "WHERE import_batch_id=?", (batch_id,),
            ):
                try:
                    out["snap"][r["invoice_no"]] = (json.loads(r["expected_json"]),
                                                    json.loads(r["actual_json"]))
                except Exception:  # noqa: BLE001 坏快照按缺处理（回退 live）
                    continue
        for r in conn.execute(
            "SELECT invoice_no, receipt_date, amount, person_name FROM collection "
            "WHERE source IN ('import','manual') AND substr(receipt_date,1,7) <= ?",
            (period,),
        ):
            out["act"].setdefault(r["invoice_no"], []).append(
                ((r["receipt_date"] or "")[:7], r["amount"], r["person_name"] or ""))
        return out
    finally:
        if own:
            conn.close()


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
        "synced": bool(r["synced"]),
        "source": f"{r['sheet_name'] or r['sheet_key'] or '—'} · 第{r['row_no'] or 0}行",
    }


def _raw_side(batch_id: int, period: str) -> List[Dict]:
    """镜表行 → 结构化源侧（保持镜表行序；同号取末行）。

    sheet3（应收账款）本批次只写镜表、不建票（方案 A，判据=票号不在库），
    但它们仍必须出现在复核表里 —— 它们是「待补录」**待办**，不是数据异常。
    故照常返回，只在行上打 `needs_backfill` 标记（票号 ∈ 库中缺号的 sheet3 票集，
    见 raw_ledger.deferred_sheet3_nos），由 build_review_rows 转成 needs_backfill
    行，供复核页以中性色单列、不计入「差异」统计（见 ReviewPostView._render）。
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, sheet_key, sheet_name, row_no, invoice_no, buyer, "
            "amount_num, handler_text, remark, synced FROM raw_ledger "
            "WHERE import_batch_id=? AND kind='invoice' ORDER BY id",
            (batch_id,),
        ).fetchall()
        deferred = rl.deferred_sheet3_nos(period, batch_id, conn=conn)
    finally:
        conn.close()
    out: Dict[str, Dict] = {}
    order: List[str] = []
    for r in rows:
        no = r["invoice_no"] or ""
        if no not in out:
            order.append(no)
        src = _derive_raw(r, period)
        src["needs_backfill"] = no in deferred
        out[no] = src  # 同号后者覆盖（与导入语义一致）
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
            f"WHERE source IN ('import','manual') AND ({cond})",
            params,
        ):
            db_inv[r["invoice_no"]] = {
                "buyer": r["buyer"] or "",
                "total_amount": r["total_amount"] or 0.0,
            }
        # 经办人分摊：import 与 manual 都读。charge_detail 上有
        # UNIQUE(invoice_no, person_name) 约束 → 同一票同一人不可能有两套行，
        # 不存在「谁覆盖谁」的歧义（同名票号已被待补录机制排除）。
        for r in conn.execute(
            f"SELECT invoice_no, person_name, billing_amount FROM charge_detail "
            f"WHERE source IN ('import','manual') AND ({cond})",
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
            "WHERE source IN ('import','manual') AND substr(receipt_date,1,7) <= ?",
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
        # 待补录待办：镜表有、库中无、且属「库中缺号的 sheet3 期外票」。
        # 与普通「仅源有」区分开，复核页不计入差异统计。
        needs = bool(s and s.get("needs_backfill")) and d_inv is None

        # ---- 已收认定双方（批 2 抽出为 `_recv_sides`，与导入前比对同一口径）----
        exp_total, exp_items, act_total, act_items = _recv_sides(no, s, snap, live_act)
        recv_src = "、".join(f"{it['ym']} {it['amount']:,.2f}" for it in exp_items) \
            or (("红字无收款" if s and s.get("is_red") else "未收款") if s else "—")
        recv_db = "、".join(f"{it['ym']} {it['amount']:,.2f}" for it in act_items) or "—"

        # ---- 逐维度差异（批 2 抽出为 `lib_diff`，与导入前 evaluate 共用同一口径）----
        flags: List[str] = []
        if s is None:
            status = "仅库有"
            flags = ["镜表无此行"]
        elif d_inv is None:
            status = "仅源有"
            flags = ["库中缺失"]
            if needs:
                flags = ["应收账款期外票，库中暂无此票，需到「补录原票」补录"]
        else:
            d = lib_diff(s, d_inv, d_handlers, exp_total, act_total)
            flags = d["flags"] if d else []
            status = "不符" if flags else "一致"

        note = notes.get(no, "")
        # 待补录待办不套用「已确认异常」：它是未完成的动作，标记确认会把待办埋掉
        if status in ("不符", "仅源有", "仅库有") and note and not needs:
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
            "needs_backfill": needs,
            "flags": flags,
            "detail": "；".join(flags),
            "confirmed_note": note,
            "raw_id": s["raw_id"] if s else None,
            "synced": s["synced"] if s else True,
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
