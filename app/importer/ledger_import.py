"""发票台账解析器（每月一本，4 个 sheet）

真实格式（2025.1-12 台账）：
- sheet 名跨月不统一，按关键词识别：已开票已入账 / 已开票未入账 / 应收账款 / 已入账未开票
- sheet1/2/3 列：序号|开票日期|发票号码|对方|金额|经办人|备注|案号
- sheet4 列：序号|收到日期|发票号码(空)|对方|金额|经办人|备注|案号
- sheet3 含期外发票（开票日期早于导入月份，需创建新发票）
"""
from __future__ import annotations

from typing import Dict, List

from app.importer.date_utils import normalize_date
from app.importer.excel_reader import ImportError_, cell_text, col_index, find_header_row, read_sheet, sheet_names
from app.importer.parse_handler import parse_handler_column
from app.importer.parse_remark import parse_remark


def norm_full_date(text: str, default_year: int | None = None) -> str:
    """台账日期 → YYYY-MM-DD（委托通用 normalize_date，支持更多写法）。"""
    return normalize_date(text, default_year=default_year)


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


def _parse_invoice_sheet(rows: List[List[str]], sheet_key: str, period: str) -> "tuple[List[Dict], List[Dict]]":
    """解析 sheet1/2/3：发票行。返回 (items, problems)；问题行不中断，收集到 problems。"""
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

    def g(row: List[str], i: int) -> str:
        return row[i].strip() if 0 <= i < len(row) else ""

    items: List[Dict] = []
    problems: List[Dict] = []
    year = int(period.split("-")[0])
    for row_idx, row in enumerate(rows[hr + 1:], start=hr + 2):  # 行号按原始表头起算（1 基）
        no = g(row, idx_no)
        if not no:
            continue

        def problem(reason: str) -> Dict:
            return {
                "kind": "invoice", "sheet": sheet_key, "row_no": row_idx,
                "invoice_no": no, "buyer": g(row, idx_buyer),
                "total_amount": g(row, idx_amt),
                "handler_text": g(row, idx_handler), "remark_raw": g(row, idx_remark),
                "date_text": g(row, idx_date), "reason": reason,
            }

        amt_txt = g(row, idx_amt)
        try:
            total = float(amt_txt.replace(",", "")) if amt_txt else 0.0
        except ValueError:
            problems.append(problem(f"金额无法解析「{amt_txt}」"))
            continue

        handler_text = g(row, idx_handler)
        try:
            handlers = parse_handler_column(handler_text, total, no)
            remark_raw = g(row, idx_remark)
            remark = parse_remark(remark_raw, default_year=year)
            rcv_date = norm_full_date(g(row, idx_rcvdate), year) if idx_rcvdate >= 0 and g(row, idx_rcvdate) else None
        except ImportError_ as e:
            problems.append(problem(str(e)))
            continue

        items.append({
            "sheet": sheet_key,
            "invoice_no": no,
            "invoice_date": norm_full_date(g(row, idx_date), year) if idx_date >= 0 else None,
            "buyer": g(row, idx_buyer),
            "total_amount": total,
            "handlers": handlers,
            "handler_text": handler_text,
            "remark_raw": remark_raw,
            "remark": remark,
            "case_no": g(row, idx_case),
            "is_red": total < 0,
        })
    return items, problems


def _parse_sheet4(rows: List[List[str]], period: str) -> "tuple[List[Dict], List[Dict]]":
    """解析 sheet4：预收款行。返回 (items, problems)。"""
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

    def g(row: List[str], i: int) -> str:
        return row[i].strip() if 0 <= i < len(row) else ""

    items: List[Dict] = []
    problems: List[Dict] = []
    year = int(period.split("-")[0])
    for row_idx, row in enumerate(rows[hr + 1:], start=hr + 2):
        buyer = g(row, idx_buyer)
        amt_txt = g(row, idx_amt)
        if not buyer and not amt_txt:
            continue

        def problem(reason: str) -> Dict:
            return {
                "kind": "prepayment", "sheet": "sheet4", "row_no": row_idx,
                "buyer": buyer, "amount_text": amt_txt,
                "person_text": g(row, idx_handler), "date_text": g(row, idx_rcvdate),
                "reason": reason,
            }

        try:
            amount = float(amt_txt.replace(",", "")) if amt_txt else 0.0
        except ValueError:
            problems.append(problem(f"金额无法解析「{amt_txt}」"))
            continue

        try:
            received_date = norm_full_date(g(row, idx_rcvdate), year) if idx_rcvdate >= 0 and g(row, idx_rcvdate) else None
        except ImportError_ as e:
            problems.append(problem(str(e)))
            continue

        items.append({
            "received_date": received_date,
            "buyer": buyer,
            "amount": amount,
            "person_text": g(row, idx_handler),
            "remark": g(row, idx_remark),
            "case_no": g(row, idx_case),
        })
    return items, problems


def parse_ledger_file(path: str, period: str) -> Dict:
    """解析发票台账，返回结构化数据（未落库）

    返回: {invoices, prepayments, sheet_totals, problems, sheet12_total}
    problems 为无法解析的问题行（解析失败不中断，收集到此列表由上层处理）。
    """
    names = sheet_names(path)
    mapping = classify_sheets(names)
    if not mapping:
        raise ImportError_("未识别到发票台账 sheet（需含'已开票已入账/已开票未入账/应收账款/已入账未开票'）")

    result: Dict = {"invoices": [], "prepayments": [], "sheet_totals": {}, "problems": []}
    for key, name in mapping.items():
        rows = read_sheet(path, sheet_name=name)
        if key in ("sheet1", "sheet2", "sheet3"):
            items, problems = _parse_invoice_sheet(rows, key, period)
            result["invoices"].extend(items)
            result["problems"].extend(problems)
            result["sheet_totals"][key] = sum(i["total_amount"] for i in items)
        elif key == "sheet4":
            items, problems = _parse_sheet4(rows, period)
            result["prepayments"] = items
            result["problems"].extend(problems)

    result["sheet12_total"] = result["sheet_totals"].get("sheet1", 0.0) + result["sheet_totals"].get("sheet2", 0.0)
    return result
