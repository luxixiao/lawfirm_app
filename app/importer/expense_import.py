"""费用台账解析器（每月一本）

真实格式（费用台账2025.1.xlsx）：
- r1 表头：序号|时间|名称|专票号码|经手人|经办人|费用金额|税额|账面费用金额|费用类型|凭证号|一级科目|二级科目
- 经手人=真实发生人员，经办人=承担人员（通常相同）；"公共"表示公共费用
- 账面费用金额 = 费用金额 - 税额（校验）
"""
from __future__ import annotations

import re
from typing import Dict, List

from app.importer.date_utils import normalize_date
from app.importer.excel_reader import ImportError_, col_index, find_header_row, read_sheet

# 真实数据中经办人可能写成"徐琦合伙"（姓名+类型拼接），清洗类型后缀
_TYPE_SUFFIX = re.compile(r"(合伙|聘用|兼职|挂靠|行政)$")


def _clean_name(s: str) -> str:
    s = (s or "").strip()
    return _TYPE_SUFFIX.sub("", s) if s else s


def parse_expense_file(path: str, period: str) -> List[Dict]:
    rows = read_sheet(path, sheet_index=0)
    hr = find_header_row(rows, ["名称", "费用金额"])
    if hr < 0:
        raise ImportError_("费用台账未找到表头（需含'名称'和'费用金额'列）")
    header = rows[hr]

    idx_seq = col_index(header, "序号")
    idx_time = col_index(header, "时间", "日期")
    idx_name = col_index(header, "名称")
    idx_ticket = col_index(header, "专票", "发票号码")
    idx_handler = col_index(header, "经手人")
    idx_actual = col_index(header, "经办人")
    idx_amount = col_index(header, "费用金额", "金额")
    idx_tax = col_index(header, "税额")
    idx_book = col_index(header, "账面", "不含税")
    idx_type = col_index(header, "费用类型", "类型")
    idx_voucher = col_index(header, "凭证号")
    idx_sub1 = col_index(header, "一级科目")
    idx_sub2 = col_index(header, "二级科目")

    items: List[Dict] = []
    for row in rows[hr + 1:]:
        def g(i: int) -> str:
            return row[i].strip() if 0 <= i < len(row) else ""

        name = g(idx_name)
        amt_txt = g(idx_amount)
        if not name and not amt_txt:
            continue

        def num(txt: str) -> float | None:
            if not txt:
                return None
            try:
                return float(txt.replace(",", ""))
            except ValueError:
                raise ImportError_(f"费用「{name}」金额无法解析: 「{txt}」") from None

        expense_amount = num(amt_txt)
        tax_amount = num(g(idx_tax))
        book_amount = num(g(idx_book))

        # 校验：账面费用金额 = 费用金额 - 税额（允许 0.01 误差）
        if expense_amount is not None and tax_amount is not None and book_amount is not None:
            if abs(book_amount - (expense_amount - tax_amount)) > 0.01:
                raise ImportError_(
                    f"费用「{name}」校验失败: 账面({book_amount:g}) ≠ 费用金额({expense_amount:g}) - 税额({tax_amount:g})"
                )

        items.append({
            "period": period,
            "seq": int(g(idx_seq)) if g(idx_seq) else None,
            "exp_date": normalize_date(g(idx_time), default_year=int(period.split("-")[0])),
            "name": name,
            "ticket_no": g(idx_ticket),
            "handler": _clean_name(g(idx_handler)),
            "actual_handler": _clean_name(g(idx_actual)),
            "expense_amount": expense_amount,
            "tax_amount": tax_amount,
            "book_amount": book_amount,
            "expense_type": g(idx_type),
            "voucher_no": g(idx_voucher),
            "subject1": g(idx_sub1),
            "subject2": g(idx_sub2),
        })

    if not items:
        raise ImportError_("费用台账没有有效数据")
    return items
