"""发票台账解析器（每月一本，4 个 sheet）

真实格式（2025.1-12 台账）：
- sheet 名跨月不统一，按关键词识别：已开票已入账 / 已开票未入账 / 应收账款 / 已入账未开票
- sheet1/2/3 列：序号|开票日期|发票号码|对方|金额|经办人|备注|案号
- sheet4 列：序号|收到日期|发票号码(空)|对方|金额|经办人|备注|案号
- sheet3 含期外发票（开票日期早于导入月份，需创建新发票）
"""
from __future__ import annotations

import re
from typing import Dict, List

from app.importer.excel_reader import ImportError_, cell_text, col_index, find_header_row, read_sheet, sheet_names
from app.importer.parse_handler import parse_handler_column
from app.importer.parse_remark import parse_remark

DATE_FULL = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
DATE_DOT4 = re.compile(r"^(\d{4})\.(\d{1,2})(?:\.(\d{1,2}))?$")
DATE_SHORT = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{1,2}))?$")


def norm_full_date(text: str, default_year: int | None = None) -> str:
    """台账日期 → YYYY-MM-DD（支持 25.1.2 / 2022.5.23 / 2025-01-02 / 1.2 缺年）"""
    text = (text or "").strip()
    m = DATE_FULL.match(text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = DATE_DOT4.match(text)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        d = int(m.group(3)) if m.group(3) else 1
        return f"{y:04d}-{mo:02d}-{d:02d}"
    m = DATE_SHORT.match(text)
    if m:
        y = int(m.group(1)) + 2000
        mo = int(m.group(2))
        d = int(m.group(3)) if m.group(3) else 1
        return f"{y:04d}-{mo:02d}-{d:02d}"
    if default_year:
        m2 = DATE_SHORT.match(f"1.{text}")  # 兜底
        if m2:
            return norm_full_date(f"{default_year}.{text}", default_year)
    raise ImportError_(f"日期无法解析: 「{text}」")


def _pick_sheet(rows: List[List[str]], keyword: str) -> List[List[str]] | None:
    """按关键词返回数据行（跳过表头）"""
    hr = find_header_row(rows, [keyword if keyword == "发票号码" else keyword[:4]])
    return rows


def classify_sheets(names: List[str]) -> Dict[str, str]:
    """把 sheet 名映射到类型：sheet1/sheet2/sheet3/sheet4"""
    mapping: Dict[str, str] = {}
    for name in names:
        if "已开票已入账" in name:
            mapping["sheet1"] = name
        elif "已开票未入账" in name:
            mapping["sheet2"] = name
        elif "应收账款" in name:
            mapping["sheet3"] = name
        elif "已入账未开票" in name:
            mapping["sheet4"] = name
    return mapping


def _parse_invoice_sheet(rows: List[List[str]], sheet_key: str, period: str) -> List[Dict]:
    """解析 sheet1/2/3：发票行"""
    hr = find_header_row(rows, ["发票号码", "经办人"])
    if hr < 0:
        raise ImportError_(f"{sheet_key} 未找到表头（需含'发票号码'和'经办人'列）")
    header = rows[hr]
    idx_date = col_index(header, "开票日期", "开具日期")
    idx_no = col_index(header, "发票号码")
    idx_buyer = col_index(header, "对方", "购方")
    idx_amt = col_index(header, "金额", "开票金额")
    idx_handler = col_index(header, "经办人")
    idx_remark = col_index(header, "备注")
    idx_case = col_index(header, "案号")
    idx_rcvdate = col_index(header, "收到日期")  # sheet4 专用，这里通常 -1

    items: List[Dict] = []
    year = int(period.split("-")[0])
    for row in rows[hr + 1:]:
        no = row[idx_no].strip() if 0 <= idx_no < len(row) else ""
        if not no:
            continue
        def g(i: int) -> str:
            return row[i].strip() if 0 <= i < len(row) else ""
        amt_txt = g(idx_amt)
        try:
            total = float(amt_txt.replace(",", "")) if amt_txt else 0.0
        except ValueError:
            raise ImportError_(f"发票 {no} 金额无法解析: 「{amt_txt}」") from None

        handler_text = g(idx_handler)
        handlers = parse_handler_column(handler_text, total, no)
        remark_raw = g(idx_remark)
        remark = parse_remark(remark_raw, default_year=year)
        rcv_date = norm_full_date(g(idx_rcvdate), year) if idx_rcvdate >= 0 and g(idx_rcvdate) else None

        items.append({
            "sheet": sheet_key,
            "invoice_no": no,
            "invoice_date": norm_full_date(g(idx_date), year) if idx_date >= 0 else None,
            "buyer": g(idx_buyer),
            "total_amount": total,
            "handlers": handlers,
            "handler_text": handler_text,
            "remark_raw": remark_raw,
            "remark": remark,
            "case_no": g(idx_case),
            "is_red": total < 0,
        })
    return items


def _parse_sheet4(rows: List[List[str]], period: str) -> List[Dict]:
    """解析 sheet4：预收款行"""
    hr = find_header_row(rows, ["金额", "经办人"])
    if hr < 0:
        raise ImportError_("已入账未开票未找到表头（需含'金额'和'经办人'列）")
    header = rows[hr]
    idx_rcvdate = col_index(header, "收到日期")
    idx_buyer = col_index(header, "对方", "购方", "汇款")
    idx_amt = col_index(header, "金额")
    idx_handler = col_index(header, "经办人")
    idx_remark = col_index(header, "备注")
    idx_case = col_index(header, "案号")

    items: List[Dict] = []
    year = int(period.split("-")[0])
    for row in rows[hr + 1:]:
        def g(i: int) -> str:
            return row[i].strip() if 0 <= i < len(row) else ""
        buyer = g(idx_buyer)
        amt_txt = g(idx_amt)
        if not buyer and not amt_txt:
            continue
        try:
            amount = float(amt_txt.replace(",", "")) if amt_txt else 0.0
        except ValueError:
            raise ImportError_(f"预收款金额无法解析: 「{amt_txt}」") from None

        items.append({
            "received_date": norm_full_date(g(idx_rcvdate), year) if idx_rcvdate >= 0 and g(idx_rcvdate) else None,
            "buyer": buyer,
            "amount": amount,
            "person_text": g(idx_handler),
            "remark": g(idx_remark),
            "case_no": g(idx_case),
        })
    return items


def parse_ledger_file(path: str, period: str) -> Dict:
    """解析发票台账，返回结构化数据（未落库）"""
    names = sheet_names(path)
    mapping = classify_sheets(names)
    if not mapping:
        raise ImportError_("未识别到发票台账 sheet（需含'已开票已入账/已开票未入账/应收账款/已入账未开票'）")

    result: Dict = {"invoices": [], "prepayments": [], "sheet_totals": {}}
    for key, name in mapping.items():
        rows = read_sheet(path, sheet_name=name)
        if key in ("sheet1", "sheet2", "sheet3"):
            items = _parse_invoice_sheet(rows, key, period)
            result["invoices"].extend(items)
            result["sheet_totals"][key] = sum(i["total_amount"] for i in items)
        elif key == "sheet4":
            result["prepayments"] = _parse_sheet4(rows, period)

    result["sheet12_total"] = result["sheet_totals"].get("sheet1", 0.0) + result["sheet_totals"].get("sheet2", 0.0)
    return result
