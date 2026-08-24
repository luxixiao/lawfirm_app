"""导入日期规范化：把台账中的各种日期写法统一为 YYYY-MM-DD

支持格式（覆盖三类台账 + 手动补录可能出现的写法）：
- 已规范       2025-01-02
- 中划线/斜杠  2025-1-2 / 2025/1/2
- 点分（4位年） 2025.1.2 / 2025.1
- 中文年月日   2025年1月2日 / 2025年1月
- 两位年点分   25.1.2 / 25.1
- 缺年（月日）  1.2 / 1月2日（需 default_year）
- 带时间      2025-01-02 00:00:00 / 2025-1-2 12:30（截断取日期）
- datetime 对象（openpyxl 日期单元格直接读出的类型）

解析失败抛 ImportError_（带原值），让导入当场报错而非写入脏数据。
"""
from __future__ import annotations

import re
from datetime import datetime

from app.importer.excel_reader import ImportError_

_FULL = re.compile(r"^(\d{4})[年/\-.](\d{1,2})[月/\-.](\d{1,2})日?$")      # 2025年1月2日 / 2025-1-2 / 2025/1/2 / 2025.1.2
_YM = re.compile(r"^(\d{4})[年/\-.](\d{1,2})月?$")                          # 2025年1月 / 2025.1 / 2025-1（缺日）
_YY_DOT = re.compile(r"^(\d{2})\.(\d{1,2})(?:\.(\d{1,2}))?$")               # 25.1.2 / 25.1
_MD = re.compile(r"^(\d{1,2})[-/.月](\d{1,2})日?$")                         # 1.2 / 1月2日（缺年）
_TS = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[\sT].*$")                   # 带时间
_OK = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _valid(y: int, mo: int, d: int) -> bool:
    """严格日历校验（如 2025-02-31 非法）"""
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return False
    try:
        datetime(y, mo, d)
        return True
    except ValueError:
        return False


def normalize_date(text, default_year: int | None = None) -> str:
    """任意常见日期写法 → YYYY-MM-DD；空串/None 原样返回；解析失败抛 ImportError_。"""
    if text is None or text == "":
        return "" if text == "" else None
    if isinstance(text, datetime):
        return text.strftime("%Y-%m-%d") if text.year >= 1900 else ""

    s = str(text).strip()
    if not s:
        return ""

    # 带时间的完整日期（openpyxl 读出的 datetime 经 cell_text 后形如 2025-01-02 00:00:00）
    m = _TS.match(s)
    if m and _valid(int(m.group(1)), int(m.group(2)), int(m.group(3))):
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 已规范（YYYY-MM-DD，需校验月/日合法，如 2025-13-01 应报错）
    m = _OK.match(s)
    if m:
        if _valid(int(m.group(1)), int(m.group(2)), int(m.group(3))):
            return s
        raise ImportError_(f"日期无法解析: 「{s}」")

    # 四位年 + 月日（中划线/斜杠/点分/中文）
    m = _FULL.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid(y, mo, d):
            return f"{y:04d}-{mo:02d}-{d:02d}"

    # 四位年 + 月（缺日，日=1）
    m = _YM.match(s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-01"

    # 两位年点分（25.1.2 → 2025-01-02）
    m = _YY_DOT.match(s)
    if m:
        y = int(m.group(1)) + 2000
        mo = int(m.group(2))
        d = int(m.group(3)) if m.group(3) else 1
        if _valid(y, mo, d):
            return f"{y:04d}-{mo:02d}-{d:02d}"

    # 缺年（月日，需 default_year，用于跨月台账里"1.2 / 1月2日"）
    if default_year:
        m = _MD.match(s)
        if m:
            mo, d = int(m.group(1)), int(m.group(2))
            if _valid(default_year, mo, d):
                return f"{default_year:04d}-{mo:02d}-{d:02d}"

    raise ImportError_(f"日期无法解析: 「{s}」")
