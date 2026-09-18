"""导入复核 — 「台账 ⇄ 库」比对共用核心（批 2 抽出）

本模块原是「导入后」页（review_post_view）的融合比对引擎（镜表 ↔ 业务表）；
导入复核统一化后，「台账 ⇄ 库」比对在**导入前**判进四态（命中 → 待确认），
比对核心被抽成共用函数，新旧两个入口用**同一套口径**：

- `lib_diff(src, d_inv, d_handlers, exp_total, act_total)`：逐维度差异
  （金额 / 经办人分摊 / 已收认定，含「源经办人纯人名 → 豁免分摊金额比对」）；
- `build_lib_context(...)`：库侧三方（invoice / charge_detail / collection /
  received_snapshot）一次读出，供逐行比对；
- `_recv_sides` / `lib_recv_totals`：已收认定双方（快照优先，缺快照回退）。

消费方：`import_confidence.evaluate`（导入前，批 2 起）。
批 4（2026-09-18）：旧「导入后」页 `build_review_rows` 及其专属管线
（`latest_batch` / `_derive_raw` / `_raw_side` / `load_confirmed_notes`）随
`review_post_view.py` 一并删除 —— 导入后查看改走 `review_rebuild` 反向重建 +
`UnifiedImportDialog(mode="post")`。

库侧读取口径：invoice / charge_detail / collection 均取 `source IN ('import',
'manual')` —— 补录（manual）是期外票唯一的销账动作，若不纳入，补录完成后该行
会永远停在「库中缺失」，与「补录原票」页互相矛盾（那边票号对齐即移出）。
sheet3（应收账款）行不参与比对（应收视角 ≠ 发票视角，批 0 §9.6；实测排除后 0 差异）。

纯逻辑模块，不依赖 Qt；测试用内存库注入 get_conn。
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from app.db import get_conn
from app.importer.importer import compute_expected_receipts

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

    批 2 抽出：`import_confidence.evaluate`（导入前比对）与旧「导入后」页
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

    比对三维：金额 / 经办人分摊 / 已收认定 —— 与导入前 `evaluate` 的原因短语
    逐字同一套规则
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
    `_recv_sides` 同一口径。
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
    快照/回退口径见 `_recv_sides` ——
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
