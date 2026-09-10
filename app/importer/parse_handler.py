"""经办人列解析器

支持格式（真实台账样例）：
- 单名全额：    胡坚
- 多人带金额：  徐志庆4500、柳立中4500        （顿号分隔）
- 多人句号分隔：周士杰2000.朱琰芳1000         （英文句号分隔，2025.12 台账实测）
- 万单位：      徐志庆8万、徐琦45000、傅强1万
- 逗号分隔：    朱云，黄宝根                  （无金额 = 平摊）
- 红字负数化：  总金额为负时，经办人金额取负（台账写正数 → 规范为负数）
- 别名归一：    管理人报酬（道兴家私）→ 管理人   （见 _normalize_handler_text）
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from app.importer.excel_reader import ImportError_

# 段分隔符：中文顿号、中文/英文逗号、分号、空白、中文句号（。）
# 另以 `\.(?!\d)` 把英文句号（.）当作分隔符——但后面紧跟数字时视为金额小数点，
# 不拆分（如 周士杰2000.5 的 .5 是小数，周士杰2000.朱琰芳1000 的 . 是分隔符）。
_SEG_SPLIT = re.compile(r"[、，,;；。\s]+|\.(?!\d)")
# 名称 + 可选金额：张三 / 张三4000 / 张三8万 / 张三1.5万 / 张三一万
_NAME = r"[\u4e00-\u9fa5·]{1,6}"
_AMT = r"\d+(?:\.\d+)?"
_SEG_WITH_AMT = re.compile(rf"^({_NAME})\s*({_AMT})\s*(万)?$")
_SEG_NAME_ONLY = re.compile(rf"^({_NAME})$")

# ---------------------------------------------------------------------------
# 经办人别名归一
# ---------------------------------------------------------------------------
# 台账里「管理人」名下经常带后缀说明（项目/案由），如
#   「管理人报酬（道兴家私）」「管理人报酬」「管理人（某某项目）」
# 这些都指向同一个经办人「管理人」（花名册中的正式姓名）。
# 归一策略：**以「管理人」开头的整段**折叠为「管理人」，后缀（含括号内容）丢弃。
# 注意：segment 级归一，不是整列文本替换 —— 「徐志庆5000、管理人报酬（道兴家私）」
# 只会把第二段折叠，第一段不受影响。
_ALIAS_PREFIX: Dict[str, str] = {
    "管理人": "管理人",
}


def _normalize_handler_text(text: str) -> str:
    """按 segment 折叠经办人别名：以「管理人」开头的段 → 「管理人」。

    - 只在段首命中别名前缀时折叠，避免「张三管理人」这类真名被误伤。
    - 保留段内显式金额（如「管理人报酬（xx）3000」仍能取到 3000）。
    - **不重写分隔符**：无别名命中时原样返回（连分隔符一起），零副作用。
    """
    text = (text or "").strip()
    if not text:
        return text

    # 无任何别名前缀出现 -> 直接返回原文（连分隔符都不动）
    if not any(p in text for p in _ALIAS_PREFIX):
        return text

    parts = _SEG_SPLIT.split(text)
    seps = _SEG_SPLIT.findall(text)
    out: List[str] = []
    for i, seg in enumerate(parts):
        if seg:
            replaced = False
            for prefix, canonical in _ALIAS_PREFIX.items():
                if seg.startswith(prefix):
                    suffix = seg[len(prefix):]
                    m = re.search(rf"({_AMT})\s*(万)?$", suffix)
                    out.append(canonical + m.group(0) if m else canonical)
                    replaced = True
                    break
            if not replaced:
                out.append(seg)
        # 原样保留分隔符（含未消费的空白），保证文本结构不变
        if i < len(seps):
            out.append(seps[i])
    return "".join(out)

_CN_NUM = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_CN_AMT_RE = re.compile(r"^([一两二三四五六七八九十]+)万$")


def _cn_amount(text: str) -> float | None:
    """中文金额（如'一万'、'十五万'）转数字；不支持则返回 None"""
    m = _CN_AMT_RE.match(text)
    if not m:
        return None
    s = m.group(1)
    if "十" in s:
        a, b = s.split("十", 1) if s != "十" else ("", "")
        val = (_CN_NUM.get(a, 0) if a else 1) * 10 + (_CN_NUM.get(b, 0) if b else 0)
    else:
        val = sum(_CN_NUM.get(c, 0) for c in s)
    return float(val) * 10000


def parse_handler_column(text: str, total_amount: float, invoice_no: str = "") -> List[Tuple[str, float]]:
    """解析经办人列

    Args:
        text: 经办人列原文（如 "徐志庆4500、柳立中4500"）
        total_amount: 该发票开票总额（用于单经办人全额 / 平摊 / 红字负数化）
        invoice_no: 发票号码（仅用于报错信息）

    Returns:
        [(name, amount), ...]
    """
    text = (text or "").strip()
    if not text:
        return []

    # 别名归一（管理人报酬（xx）→ 管理人）；无命中时原样返回，零副作用
    text = _normalize_handler_text(text)

    segs = [s for s in _SEG_SPLIT.split(text) if s]
    if not segs:
        return []

    items: List[Tuple[str, float | None]] = []  # amount=None 表示未指定
    for seg in segs:
        m = _SEG_WITH_AMT.match(seg)
        if m:
            name, num, wan = m.group(1), m.group(2), m.group(3)
            amount = float(num) * 10000 if wan else float(num)
            items.append((name, amount))
            continue
        m = _SEG_NAME_ONLY.match(seg)
        if m:
            items.append((m.group(1), None))
            continue
        # 尝试中文金额（如 "一万" 单独成段）
        cn = _cn_amount(seg)
        if cn is not None:
            items.append(("", cn))  # 无名称的金额段（异常，后续报错）
            continue
        raise ImportError_(f"发票 {invoice_no} 经办人列无法解析: 「{text}」（段: {seg}）")

    # 无名称的金额段 → 报错
    if any(name == "" for name, _ in items):
        raise ImportError_(f"发票 {invoice_no} 经办人列缺少姓名: 「{text}」")

    # 确定金额（amounts[i] 与 items[i] 对应；amt_specified 记录是否显式写了金额）
    amt_specified = [a is not None for _, a in items]
    specified = [a for _, a in items if a is not None]
    unspecified_idx = [i for i, s in enumerate(amt_specified) if not s]

    if not specified:
        # 全部无金额：单经办人占全额；多经办人平摊
        if len(items) == 1:
            amounts: List[float] = [total_amount]
        else:
            share = total_amount / len(items)
            amounts = [share] * len(items)
    elif unspecified_idx:
        # 部分指定：未指定的平摊剩余
        rest = total_amount - sum(specified)
        share = rest / len(unspecified_idx)
        amounts = []
        for i, (_, a) in enumerate(items):
            amounts.append(a if a is not None else share)
    else:
        amounts = [a for a in specified]  # type: ignore[assignment]

    # 红字发票（总额为负）：只对显式写正数金额的经办人取负（台账常写正数）
    # 全额/平摊已按负总额计算，不能二次取负
    if total_amount < 0:
        amounts = [-a if amt_specified[i] else a for i, a in enumerate(amounts)]  # type: ignore[operator]

    result = [(name, float(amt)) for (name, _), amt in zip(items, amounts)]

    # 校验：经办人合计 = 开票总额（允许 0.01 误差）
    total = sum(a for _, a in result)
    if abs(total - total_amount) > 0.01:
        raise ImportError_(
            f"发票 {invoice_no} 经办人金额合计({total:g}) ≠ 开票总额({total_amount:g}): 「{text}」"
        )
    return result
