"""发票台账备注列解析器

真实样例（2025.1-12 台账）：
- 空                  → 未收款
- 25.1.2              → 全额收款（取最早日期）
- 24.12.20-21         → 日期范围，取最早日期，全额收款
- 25.1.16冲掉          → 该正数发票被红冲（非收款）
- 冲24.11.4发票2433... → 红冲信息（非收款，红冲关联以销项备注为准）
- 25.2.5开             → sheet4 预计开票（非收款）
- 25.10.24收3000,10.31收2000            → 多笔收款
- 25.8.28收到16000，25.10.31收8000，还剩2.2万未付
- 25.2.27收15万，2.28收25万
- 24.10.15收一万，25.7.9收1万
- 25.7.22收1050，还剩28950未收
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from app.importer.excel_reader import ImportError_

_SEG_SPLIT = re.compile(r"[，,、;；]+")
_DATE = r"\d{1,2}\.\d{1,2}(?:\.\d{1,2})?"
# 纯日期（可带省略月份的日范围，如 "24.12.20-21"）
_PURE_DATE = re.compile(rf"^{_DATE}(?:-\d{{1,2}}(?:\.\d{{1,2}})?)?$")
_RED_REMARK = re.compile(r"^冲.*发票")
_RED_OFF = re.compile(r"冲掉")
_RECEIPT = re.compile(
    rf"(?:(?P<date>{_DATE})\s*)?(?P<verb>收到|收|汇|到|付)\s*(?P<amt>\d+(?:\.\d+)?万?|[\u4e00-\u9fa5]+万)"
)
_AMT_NUM = re.compile(r"^(\d+(?:\.\d+)?)\s*(万)?$")
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_REMAIN = re.compile(r"剩[余]?\s*[,，]?\s*([\d.]+万?|[\u4e00-\u9fa5]+万)\s*未[收付]")


def _cn_amount(text: str) -> float | None:
    m = re.match(r"^([一两二三四五六七八九十]+)万$", text)
    if not m:
        return None
    s = m.group(1)
    if "十" in s:
        a, b = s.split("十", 1) if s != "十" else ("", "")
        val = (_CN_NUM.get(a, 0) if a else 1) * 10 + (_CN_NUM.get(b, 0) if b else 0)
    else:
        val = sum(_CN_NUM.get(c, 0) for c in s)
    return float(val) * 10000


def _amount(text: str) -> float | None:
    m = _AMT_NUM.match(text.strip())
    if m:
        return float(m.group(1)) * (10000 if m.group(2) else 1)
    cn = _cn_amount(text.strip())
    return cn


def _norm_date(part: str, inherit_year: int | None) -> str:
    """日期段 → YYYY-MM（精确到月，两位年补 20xx，缺年继承）"""
    nums = [int(x) for x in re.split(r"\.", part)]
    if len(nums) == 2:  # 缺年
        y = inherit_year
        if y is None:
            raise ImportError_(f"备注日期缺少年份且无法继承: {part}")
        m = nums[0]
    else:
        y = nums[0] + 2000 if nums[0] < 100 else nums[0]
        m = nums[1]
    return f"{y:04d}-{m:02d}"


def _parse_date_token(token: str) -> Tuple[str, str] | None:
    """从 token 中提取日期，返回 (raw, YYYY-MM)；无日期返回 None"""
    m = re.search(_DATE, token)
    if not m:
        return None
    return m.group(0), _norm_date(m.group(0), None)


def parse_remark(text: str | None, default_year: int | None = None) -> Dict:
    """解析备注列

    Returns:
        dict(
            receipts: List[(YYYY-MM, amount)]   收款记录；amount=0 表示"全额"（调用方填开票总额）
            remaining: float | None             剩余未收（备注写了"还剩"才有）
            pure_date: str | None               纯日期全额收款的日期(YYYY-MM)
            is_red_remark: bool                 红冲信息（"冲XX发票XX"）
            is_red_off: bool                    被红冲标记（"X冲掉"）
        )
    """
    text = (text or "").strip()
    result = {
        "receipts": [],
        "remaining": None,
        "pure_date": None,
        "is_red_remark": False,
        "is_red_off": False,
    }
    if not text:
        return result

    # 红冲信息 / 被红冲标记
    if _RED_REMARK.match(text):
        result["is_red_remark"] = True
        return result
    if _RED_OFF.search(text):
        result["is_red_off"] = True
        return result

    # 剩余未收（整句检测）
    rm = _REMAIN.search(text)
    if rm:
        amt = _amount(rm.group(1))
        result["remaining"] = amt

    # 纯日期（全额收款，取最早日期）
    if _PURE_DATE.match(text):
        first = text.split("-")[0]
        result["pure_date"] = _norm_date(first, default_year)
        result["receipts"] = [(result["pure_date"], 0.0)]  # 0 = 全额
        return result

    # 收款记录列表
    inherit_year = default_year
    for seg in _SEG_SPLIT.split(text):
        seg = seg.strip()
        if not seg or _RED_OFF.search(seg) or _REMAIN.search(seg) or seg in ("还剩", "剩余"):
            continue
        m = _RECEIPT.search(seg)
        if not m:
            continue  # 无法识别的段（如"25.2.5开"），忽略
        raw_date = m.group("date")
        if raw_date:
            ym = _norm_date(raw_date, inherit_year)
            inherit_year = int(ym[:4])
        else:
            # 无日期的收款段（如"收3000"）：真实台账均带日期，直接忽略
            continue
        amt = _amount(m.group("amt"))
        if amt is None:
            raise ImportError_(f"收款金额无法解析: 「{seg}」")
        result["receipts"].append((ym, amt))

    return result
