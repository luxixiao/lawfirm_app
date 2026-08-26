"""经办人收款分摊引擎

规则（已与需求方确认）：
- 全额收款：经办人已收 = 经办人开票金额
- 部分收款：按收款记录时间顺序逐笔处理——
  每笔收款在"未收齐"经办人间平均分摊；某经办人累计已收达到其开票金额即收齐、
  退出分摊池；平均分摊超出其开票金额的部分转给其他未收齐经办人
- 分摊结果支持手动修改（特殊情况人工调整，存 charge_detail 的已收覆盖）
"""
from __future__ import annotations

from typing import Dict, List, Tuple


def allocate_receipt(remaining: Dict[str, float], amount: float) -> Dict[str, float]:
    """把一笔收款在未收齐经办人间平均分摊

    Args:
        remaining: {经办人: 剩余应收份额}（会被原地更新）
        amount: 本次收款金额

    Returns:
        {经办人: 本次分到的金额}
    """
    if amount <= 0:
        return {}
    pool = [n for n in remaining if remaining[n] > 0.01]
    result: Dict[str, float] = {}
    while amount > 0.01 and pool:
        share = amount / len(pool)
        consumed = 0.0
        next_pool: List[str] = []
        for n in pool:
            if remaining[n] <= share + 0.01:
                # 该经办人收齐，退出分摊池
                give = remaining[n]
                result[n] = result.get(n, 0.0) + give
                remaining[n] = 0.0
                consumed += give
            else:
                give = share
                result[n] = result.get(n, 0.0) + give
                remaining[n] -= give
                consumed += give
                next_pool.append(n)
        amount -= consumed
        pool = next_pool
    return result


def allocate_invoice(
    handlers: List[Tuple[str, float]],
    receipts: List[Tuple[str, float]],
    overrides: Dict[str, float] | None = None,
) -> List[Tuple[str, float]]:
    """按规则分摊整张发票的收款到经办人

    Args:
        handlers: [(经办人, 开票金额)]（红字发票为负）
        receipts: [(收款日期, 金额)] 按时间顺序；金额>0 为收款
                  （红字发票的"退款"金额为负，不参与分摊，由 refund 单独处理）
        overrides: {经办人: 已收覆盖值}（可空）。非空的经办人直接用覆盖值；
                   其余经办人分摊剩余已收（按开票金额比例，封顶开票金额）。
                   传 None 时走原始逐笔分摊算法（保持历史行为完全一致）。

    Returns:
        [(经办人, 已收金额)]
    """
    # 红字发票：经办人开票金额为负，不适用本分摊（退款走 refund 手动确认）
    if any(billing < 0 for _, billing in handlers):
        return [(name, 0.0) for name, _ in handlers]

    overrides = overrides or {}

    if not overrides:
        # 原始算法（无覆盖）：逐笔收款在"未收齐"经办人间平均分摊
        received: Dict[str, float] = {name: 0.0 for name, _ in handlers}
        remaining: Dict[str, float] = {name: billing for name, billing in handlers}
        for _, amount in receipts:
            got = allocate_receipt(remaining, amount)
            for n, v in got.items():
                received[n] += v
        return [(name, received[name]) for name, _ in handlers]

    # 有覆盖值：被覆盖经办人直接用覆盖值，其余按开票金额比例分摊剩余已收
    received: Dict[str, float] = {}
    overridden_total = 0.0
    for name, billing in handlers:
        if name in overrides:
            received[name] = overrides[name]
            overridden_total += overrides[name]
        else:
            received[name] = 0.0
    total_received = sum(a for _, a in receipts)
    remaining_amt = max(0.0, total_received - overridden_total)
    non = [(n, b) for n, b in handlers if n not in overrides and b > 0.01]
    if non:
        total_billing = sum(b for _, b in non)
        for n, b in non:
            share = remaining_amt * (b / total_billing)
            received[n] = min(share, b)  # 封顶在开票金额，避免超额
    return [(name, received.get(name, 0.0)) for name, _ in handlers]


def total_received(receipts: List[Tuple[str, float]]) -> float:
    """发票已收金额（所有收款之和；红字发票由退款负值体现）"""
    return sum(a for _, a in receipts)
