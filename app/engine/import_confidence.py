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

另（阶段 6，**不由 `evaluate` 调用**，供复核页/补录弹窗按需调用）：
  `red_orig_diff()` 比对红字发票与其引用的蓝字原票的「总金额 / 经办人 / 经办人金额」
  —— 金额一律取绝对值，故红字 -5000 与蓝字 5000 属一致。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from app.engine.split import allocate_invoice

SHEET_LABEL = {
    "sheet1": "已开票已入账",
    "sheet2": "已开票未入账",
    "sheet3": "应收账款",
    "sheet4": "已入账未开票",
    "problem_fix": "问题行修正",
}


def _to_ym(s: str) -> str:
    """把各种形态的账期/日期归一为 'YYYY-MM'；无法解析返回空串。

    兼容 2025-12 / 2025.12 / 25.12 / 2025年12月 等写法（2 位年补 2000）。
    """
    if not s:
        return ""
    s = str(s).strip()
    m = re.search(r"(\d{4})[-./\u5e74](\d{1,2})", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(\d{2})[-./\u5e74](\d{1,2})", s)
        if not m:
            return ""
        y, mo = int(m.group(1)) + 2000, int(m.group(2))
    if 1 <= mo <= 12:
        return f"{y:04d}-{mo:02d}"
    return ""


# 批 2：台账 ⇄ 库 比对（导入前只比金额）命中时的原因列前缀。
# 差异维度名与对照值由 `review_compare.lib_diff` 产出（唯一口径，旧页共用）。
REASON_LIB_DIFF = "台账⇄库不一致（{}），请确认"


def evaluate(data: Dict, staff_set: set, confirmed: set = None, period: str = None,
             lib: Optional[Dict] = None) -> List[Dict]:
    """评估全部发票，返回逐张结果列表。

    Args:
        data: parse_ledger_file(...) 的解析结果（含 invoices）。
        staff_set: 在职职工姓名集合（用于"不在花名册"判定）。
        confirmed: 已被用户「确认」过的发票下标集合（下标对应 data.invoices 顺序）。
                   命中后，sheet1/2/3 的三类「兜底判定」提示不再追加，视为用户已认可
                   系统默认口径（例如 sheet1 空备注按全额收款、应收账款纯日期按全额等）。
        period: 导入账期（"YYYY-MM"）。用于校验应收账款(sheet3)备注收款日期是否落在当月；
                缺省时回退到 data.get("period")，仍取不到则跳过该月校验。
        lib: 批 2「台账 ⇄ 库」比对的**库侧上下文**（`review_compare.build_lib_context`
             的产物）。`None`（缺省）→ 完全跳过比对，行为与批 1 一致；
             调用方（复核页）**只在导入前模式传入** —— 导入后镜表原文与库的差异正是
             导入时人工修正的痕迹，再报一遍就是把毛病 1 复活成点不掉的假待办。
             两道门槛（2026-09-18 用户拍板）：
             **① 只比「票已在库」的行** —— 票不在库 = 本批正要写入它（当期 sheet1/2
             全量如此），**不是差异**；
             **② 只比「金额」维度**（`dims=(FIELD_AMOUNT,)`）—— 「票在库」≠「库侧有
             可比数据」：`charge_detail` / `collection` 就是本次台账要写的，导入那一刻
             必然为空，比它们 = 拿「缺数据」当「不一致」（实测当期 86/86 假报）。
             理由详见批 0 §5.5/§6 与 `docs/…batch0…md` §18。

    Returns: 每项为一张发票的评估结果 dict，含 inv 引用（_inv）。
    """
    confirmed = confirmed or set()
    period = period or data.get("period") or ""
    period_ym = _to_ym(period)
    results: List[Dict] = []
    for i, inv in enumerate(data.get("invoices", [])):
        sheet = inv.get("sheet") or ""
        total = inv.get("total_amount") or 0.0
        is_red = bool(inv.get("is_red"))
        handlers: List[Tuple[str, float]] = inv.get("handlers") or []
        remark = inv.get("remark") or {}
        reasons: List[str] = []
        # 用户是否逐经办人显式填过收款（问题修正/右侧表单保存都会落到 split_receipts）。
        # 已填 → 视作用户已认可系统按其所填收款认定，兜底类「请确认」提示不再追加。
        split_receipts = inv.get("split_receipts") or []
        has_split = bool(split_receipts)
        suppressed = has_split or (i in confirmed)

        # ---- 收款认定疑问 ----
        if not is_red:
            pure_date = remark.get("pure_date")
            receipts = remark.get("receipts") or []
            remaining = remark.get("remaining")
            if sheet == "sheet3" and pure_date:
                date_ym = _to_ym(pure_date)
                if period_ym and date_ym and date_ym != period_ym:
                    reasons.append("应收账款非本月收款")
                elif not suppressed:
                    reasons.append("应收账款纯日期按全额收款，请确认")
            if sheet == "sheet2" and pure_date and not suppressed:
                reasons.append("已开票未入账纯日期按未收，请确认")
            if remaining is not None and receipts and not pure_date:
                recv_sum = sum(a for _, a in receipts if a > 0)
                if abs((recv_sum + remaining) - total) > 0.01:
                    reasons.append("收款+剩余≠价税合计（勾稽不平）")
            if sheet == "sheet1" and not receipts and not pure_date and not suppressed:
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

        # ---- 批 2：台账 ⇄ 库 比对（导入前**只比金额**）----
        # 口径与共用核心 `review_compare.lib_diff` **逐字同一套**，但用 `dims` 限定维度。
        # sheet3（应收账款）行排除：同一票号可能与 sheet2 并存且收款声明不同
        # （应收视角 vs 发票视角，批 0 §9.6），实测 12 期：不排除会报 127 行
        # 「已收认定不符」，排除后 **0 差异**。
        # 消除机制与兜底类不同：**只吃「确认」**（`i in confirmed` → 3-1 留痕），
        # 不吃 `has_split` 豁免 —— 用户填了逐人收款 ≠ 认可台账金额与库一致。
        # ⚠️ 两道门槛（2026-09-18 用户拍板，批 0 §5.5/§6 + 实测）：
        # ① **只比「票已在库」的行**：票不在库 = 本批正要写入它（当期 sheet1/2 全量
        #    如此）→ 旧写法把它们全报成「库中缺失，请确认」（假待办成灾）。
        # ② **只比「金额」维度**：「票在库」≠「库侧有可比数据」—— `charge_detail` /
        #    `collection` 正是本次台账导入要写的，导入那一刻必然为空 ⇒ 比它们等于拿
        #    「缺数据」当「不一致」。实测（真实库 2025-01：销项已导 86 票、台账未导）
        #    三维全比 → 当期 **86/86（100%）** 被判待确认（分摊 86 + 已收 67），
        #    只比金额 → **0**（销项 ⇄ 台账金额完全一致，这才是两个独立来源的交叉校验）。
        #    另两维退出导入前比对（与阶段 6-2b「缺数据≠不一致」同一条铁律）。
        if lib is not None and sheet != "sheet3" and i not in confirmed:
            no2 = (inv.get("invoice_no") or "").strip()
            d_inv = lib["inv"].get(no2) if no2 else None
            if no2 and d_inv is not None:
                # 局部导入：避免环
                from app.engine.review_compare import FIELD_AMOUNT, lib_diff
                src = {
                    "invoice_no": no2,
                    "buyer": inv.get("buyer", ""),
                    "total_amount": total,
                    "is_red": is_red,
                    "handler_text": inv.get("handler_text", ""),
                    "handlers": {n: a for n, a in handlers},
                    "remark": remark,
                }
                d = lib_diff(src, d_inv, dims=(FIELD_AMOUNT,))
                if d:
                    reasons.append(REASON_LIB_DIFF.format("、".join(d["fields"]))
                                   + "〔" + "；".join(d["detail"]) + "〕")

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


# ---------------------------------------------------------------------------- #
# 阶段 6：红字发票 ⇄ 蓝字原票 一致性比对（纯函数，无 DB）
# ---------------------------------------------------------------------------- #
def _handler_amounts(handlers) -> Dict[str, float]:
    """经办人 → {姓名: 开票金额（绝对值合计）}。

    兼容本项目内部并存的两种形态：
    - 解析侧 / `ev`：`[(name, amount), ...]` —— 红字票的 amount 是**负数**；
    - 库侧 / 补录 payload：`[{"name":.., "billing":..}, ...]`。

    一律取绝对值：红字 -5000 与蓝字 5000 属**一致**（用户 2026-09-18 口径）。
    同名多行按姓名聚合，与 `backfill_module.build_backfill` 的 charge 聚合同口径。
    """
    out: Dict[str, float] = {}
    for h in handlers or ():
        if isinstance(h, dict):
            name = str(h.get("name") or "").strip()
            amt = h.get("billing", 0.0)
        else:
            seq = list(h)
            name = str(seq[0] if seq else "").strip()
            amt = seq[1] if len(seq) > 1 else 0.0
        if not name:
            continue
        try:
            out[name] = out.get(name, 0.0) + abs(float(amt or 0.0))
        except (TypeError, ValueError):
            continue
    return out


def red_orig_diff(red_total, red_handlers, orig_total, orig_handlers, *,
                  orig_persons_known: bool = True) -> Dict | None:
    """比对红字发票与其引用的蓝字原票；**一致返回 None**，不一致返回描述。

    比对三项（用户 2026-09-18 指定，**不含购方**）：
      1. 发票总金额（取绝对值）；
      2. 经办人（姓名集合）；
      3. 经办人金额（逐人、取绝对值）。

    返回 `{"fields": ["总金额", "经办人金额"], "detail": "多行对照文案"}`：
    - `fields` → 原因列短文案（「红字与蓝字原票不一致（总金额），请确认」）；
    - `detail` → 提示框逐项列出两侧实际值。

    用途有二（阶段 6）：① 台账红字行 vs **库中已有**蓝字原票；② 台账红字行 vs
    **本次补录**的蓝字原票（保存补录信息时提示）。

    `orig_persons_known=False`（阶段 6-2b）：**蓝字侧拿不到经办人分摊数据** ——
    只比总金额，**跳过「经办人 / 经办人金额」两项**。
    理由（用户 2026-09-18 口径）：结论「不一致」只能在**两侧都有数据**时下；
    蓝字侧没有 `charge_detail` 是**缺数据**，绝不能被当成差异报出来（否则误导排查）。
    正常流程下蓝字在库 ⟹ 它自己那期台账已导入（按期顺序导入且不漏期）⟹ 分摊必在，
    故此开关是**零成本防御**，不改变常规路径的行为。
    """
    dims: List[str] = []
    lines: List[str] = []

    rt, ot = abs(float(red_total or 0.0)), abs(float(orig_total or 0.0))
    if abs(rt - ot) > 0.01:
        dims.append("总金额")
        lines.append(f"发票总金额：红字 {rt:,.2f}　蓝字 {ot:,.2f}")

    rmap = _handler_amounts(red_handlers)
    omap = _handler_amounts(orig_handlers) if orig_persons_known else {}
    if orig_persons_known:
        rset, oset = set(rmap), set(omap)
        if rset != oset:
            dims.append("经办人")
            lines.append("经办人：红字 " + ("、".join(sorted(rset)) or "—")
                         + "　蓝字 " + ("、".join(sorted(oset)) or "—"))

        # 只对**两侧都有**的人比金额；姓名集合本身不同已由上一项报出，不重复刷屏
        for n in sorted(rset & oset):
            if abs(rmap[n] - omap[n]) > 0.01:
                if "经办人金额" not in dims:
                    dims.append("经办人金额")
                lines.append(f"经办人金额：{n} 红字 {rmap[n]:,.2f}　蓝字 {omap[n]:,.2f}")

    if not dims:
        return None
    return {"fields": dims, "detail": "\n".join(lines)}


def receipt_summary(inv: Dict) -> str:
    """收款认定摘要（公开入口）。

    与 `_receipt_text` 同一实现：复核页里**不是 invoice 行**的条目（应收账款 deferred 行）
    也要显示「收款认定」列，直接复用同一口径，避免两处文案漂移。
    """
    return _receipt_text(inv)


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
