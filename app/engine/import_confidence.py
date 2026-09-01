"""导入前置信度评估（方案 D）

对发票台账解析结果逐张评估「收款认定」与「经办人分摊」两个维度：
- 高置信：系统规则已能可靠判定，导入时自动过、不打扰；
- 低置信（待确认）：无法识别或有疑问，列在「待确认」清单里供人工确认。

同时给出系统预填的每经办人已收金额（allocate_invoice 推导），供确认界面
默认填入，用户可在此之上直接改每经办人已收（选项 2）。

疑点口径：
  收款疑问：应收账款(sheet3)纯日期、已开票未入账(sheet2)纯日期、
            含"还剩"勾稽不平、已开票已入账(sheet1)空备注全额兜底。
  经办人疑问：空经办人、重复经办人、不在花名册（防御性，导入校验已拦）、
            分摊合计≠发票总额（防御性，解析已校验）。
  推导疑点：分摊后某经办人已收>其开票金额（多为人工覆盖后越界）。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from app.engine.split import allocate_invoice

SHEET_LABEL = {
    "sheet1": "已开票已入账",
    "sheet2": "已开票未入账",
    "sheet3": "应收账款",
    "sheet4": "已入账未开票",
    "problem_fix": "问题行修正",
}


def evaluate(data: Dict, staff_set: set) -> List[Dict]:
    """评估全部发票，返回逐张结果列表。

    Args:
        data: parse_ledger_file(...) 的解析结果（含 invoices）。
        staff_set: 在职职工姓名集合（用于"不在花名册"判定）。

    Returns: 每项为一张发票的评估结果 dict，含 inv 引用（_inv）。
    """
    results: List[Dict] = []
    for inv in data.get("invoices", []):
        sheet = inv.get("sheet") or ""
        total = inv.get("total_amount") or 0.0
        is_red = bool(inv.get("is_red"))
        handlers: List[Tuple[str, float]] = inv.get("handlers") or []
        remark = inv.get("remark") or {}
        reasons: List[str] = []

        # ---- 收款认定疑问 ----
        if not is_red:
            pure_date = remark.get("pure_date")
            receipts = remark.get("receipts") or []
            remaining = remark.get("remaining")
            if sheet == "sheet3" and pure_date:
                reasons.append("应收账款纯日期按全额收款，请确认")
            if sheet == "sheet2" and pure_date:
                reasons.append("已开票未入账纯日期按未收，请确认")
            if remaining is not None and receipts and not pure_date:
                recv_sum = sum(a for _, a in receipts if a > 0)
                if abs((recv_sum + remaining) - total) > 0.01:
                    reasons.append("收款+剩余≠价税合计（勾稽不平）")
            if sheet == "sheet1" and not receipts and not pure_date:
                reasons.append("已开票已入账空备注按全额兜底，请确认")

        # ---- 经办人疑问 ----
        names = [n for n, _ in handlers]
        if not names:
            reasons.append("无经办人")
        else:
            if len(set(names)) < len(names):
                reasons.append("经办人重复")
            missing = sorted({n for n in set(names) if n not in staff_set})
            if missing:
                reasons.append("经办人不在花名册：" + "、".join(missing))
            if abs(sum(a for _, a in handlers) - total) > 0.01:
                reasons.append("经办人分摊合计≠价税合计")

        # ---- 系统预填每经办人已收 ----
        # 问题行修正时用户逐经办人填写的收款落在 split_receipts（备注 receipts 为空），
        # 必须直接归因，不能走备注推导，否则修正后界面仍显示「未收款」。
        split_receipts = inv.get("split_receipts") or []
        if is_red:
            system_received = {n: 0.0 for n, _ in handlers}
        elif split_receipts:
            agg = {}
            for name, amt, _ym in split_receipts:
                agg[name] = agg.get(name, 0.0) + amt
            system_received = {n: agg.get(n, 0.0) for n, _ in handlers}
            for n, b in handlers:
                got = system_received.get(n, 0.0)
                if got > b + 0.01:
                    reasons.append(f"经办人{n}已收({got:g})>开票金额({b:g})")
        else:
            # 备注 receipts 中 0.0 是「全额」哨兵，需展开为总价后再分摊
            recs = [
                (ym, amt if amt > 0 else total)
                for ym, amt in remark.get("receipts") or []
            ]
            allocated = allocate_invoice(handlers, recs)
            system_received = {n: amt for n, amt in allocated}
            for n, b in handlers:
                got = system_received.get(n, 0.0)
                if got > b + 0.01:
                    reasons.append(f"经办人{n}已收({got:g})>开票金额({b:g})")

        conf = "low" if reasons else "high"
        results.append({
            "invoice_no": inv.get("invoice_no", ""),
            "sheet": sheet,
            "sheet_label": SHEET_LABEL.get(sheet, sheet),
            "row_no": inv.get("row_no") or 0,
            "buyer": inv.get("buyer", ""),
            "total_amount": total,
            "is_red": is_red,
            "handlers": handlers,
            "receipt_text": _receipt_text(inv),
            "conf": conf,
            "reasons": reasons,
            "system_received": system_received,
            "_inv": inv,
        })
    return results


def _receipt_text(inv: Dict) -> str:
    """收款认定摘要（与预览框一致）。

    问题行修正时用户逐经办人填写的收款落在 split_receipts。为与正常行显示口径一致
    （正常行「收款认定」列只列 年月+金额/全额、不列经办人姓名），这里按年月归集、
    全额收款显示「全额」，不再带经办人姓名。
    """
    if inv.get("is_red"):
        return "红字，不产生收款"
    split = inv.get("split_receipts") or []
    if split:
        by_ym = defaultdict(float)
        for _name, amt, ym in split:
            by_ym[ym] += amt
        total = inv.get("total_amount") or 0.0
        parts = []
        for ym in sorted(by_ym):
            amt = by_ym[ym]
            if abs(amt - total) <= 0.01:
                parts.append(f"{ym} 全额")
            else:
                parts.append(f"{ym} {amt:,.2f}")
        return "、".join(parts)
    rem = inv.get("remark") or {}
    if rem.get("pure_date"):
        return f"{rem['pure_date']} 全额"
    if rem.get("receipts"):
        parts = []
        for ym, amt in rem["receipts"]:
            parts.append(f"{ym} " + ("全额" if amt == 0 else f"{amt:,.2f}"))
        return "、".join(parts)
    return "未收款"
