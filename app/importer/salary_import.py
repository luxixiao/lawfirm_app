"""工资表解析（.xls / .xlsx 通用）

源表格：浙江震天律师事务所「律师分成报酬预发」，含 3 个 sheet（列结构互不相同）：
- 聘用律师：编号/姓名/分成报酬/代扣个所税/代扣公积金/实发金额/养/医疗/失业；
           同 sheet 内还会出现**第二套表头**（金额列名变为「工资」），构成第二个数据块。
- 合伙人：  编号/姓名/预发经营所得/实发金额。
- 后勤 (实)：编号/姓名/工资/代扣个所税/代扣公积金/实发金额/养/医疗/失业。

约定（按需求方确认）：
- **账期一律以文件名为准**（period 由调用方传入），不读表内中文年月——
  实测各 sheet 表内期间互不相同（2025-01 / 2025-11 / 2024-12）且与文件名不一致。
- 保留所有原始列、不归一化：金额存「原始文本 + 数值」双写；
  「养/医疗/失业」可能是文本（如「退休」），只存原始文本。
- 自动跳过：标题行、比例行（0.08/0.01/0.005）、合计行、大写金额行、空行。
- 姓名去对齐空格：「傅  强」->「傅强」，便于与员工花名册关联。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from app.importer.excel_reader import read_sheet, sheet_names

# sheet 名 -> sheet_key（包含匹配，兼容「后勤 (实)」这类带后缀的原始名）
SHEET_KEY_RULES = [
    ("聘用律师", "lawyer"),
    ("合伙人", "partner"),
    ("后勤", "logistics"),
]

# 表头别名 -> raw_salary 字段
HEADER_ALIASES = {
    "编号": "seq",
    "姓名": "staff_name",
    "分成报酬": "share_raw",
    "工资": "salary_raw",
    "预发经营所得": "partner_raw",
    "代扣个所税": "tax_raw",
    "代扣公积金": "fund_raw",
    "实发金额": "net_raw",
    "养": "pension_raw",
    "医疗": "medical_raw",
    "失业": "unemployment_raw",
}

# 表头必需列（用于识别表头行）
_HEADER_MUST = ("编号", "姓名")

# 金额项目识别顺序（先匹配到的为准）：表头出现哪个金额列名 -> item_type
_ITEM_TYPE_BY_HEADER = [
    ("分成报酬", "分成报酬"),
    ("预发经营所得", "预发经营所得"),
    ("工资", "工资"),
]


def _parse_num(txt) -> float:
    """原始文本 -> 数值（取首个数字；非数字如「退休」返回 0.0）。"""
    if txt is None or txt == "":
        return 0.0
    if isinstance(txt, (int, float)):
        return float(txt)
    m = re.search(r"-?\d[\d,]*\.?\d*", str(txt))
    if not m:
        return 0.0
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return 0.0


def _norm_name(txt: str) -> str:
    """姓名去对齐空格：「傅  强」->「傅强」。"""
    return re.sub(r"\s+", "", (txt or "").strip())


def _sheet_key_of(name: str) -> Optional[str]:
    for kw, key in SHEET_KEY_RULES:
        if kw in name:
            return key
    return None


def _cells(row) -> List[str]:
    return [str(c or "").strip() for c in row]


def _looks_like_header(row) -> bool:
    joined = "".join(_cells(row))
    return all(k in joined for k in _HEADER_MUST)


def _is_end_row(row) -> bool:
    """合计行 / 大写金额行 -> 该数据块结束。

    只判断首格含「合计」或「人民币」（如「合计:」「合计人民币：壹万玖仟...」）。
    整行空不在此结束（数据中间可能夹空行），由调用方 continue 跳过。
    """
    cells = _cells(row)
    first = cells[0] if cells else ""
    return ("合计" in first) or ("人民币" in first)


def _map_columns(header) -> Dict[str, int]:
    cols: Dict[str, int] = {}
    for i, h in enumerate(header):
        h = str(h or "").strip()
        if h in HEADER_ALIASES:
            cols[HEADER_ALIASES[h]] = i
    return cols


def _item_type_of(header) -> str:
    joined = "".join(_cells(header))
    for kw, label in _ITEM_TYPE_BY_HEADER:
        if kw in joined:
            return label
    return ""


def _find_blocks(rows) -> List[Tuple[int, str]]:
    """找出 sheet 内所有数据块：[(表头行号, item_type), ...]。

    聘用律师 sheet 内含两套表头（分成报酬块 + 工资块），故可能返回多项。
    """
    blocks: List[Tuple[int, str]] = []
    for i, row in enumerate(rows):
        if _looks_like_header(row):
            blocks.append((i, _item_type_of(row)))
    return blocks


def parse_salary_file(path: str, period: str = "") -> Dict:
    """解析工资表文件 -> {"items": [...], "sheet_count": n, "period": period}。

    items 每行字段与 raw_salary 列一一对应（不含 id / import_batch_id / created_at）。
    """
    items: List[Dict] = []
    sheet_count = 0
    for name in sheet_names(path):
        key = _sheet_key_of(name)
        if key is None:
            continue
        rows = read_sheet(path, sheet_name=name)
        if not rows:
            continue
        sheet_count += 1
        for block_no, (hdr_idx, item_type) in enumerate(_find_blocks(rows), start=1):
            header = rows[hdr_idx]
            cols = _map_columns(header)
            if "seq" not in cols and "staff_name" not in cols:
                continue
            for r in range(hdr_idx + 1, len(rows)):
                row = rows[r]
                # 下一套表头出现 -> 本块结束（交给下一个 block 处理）
                if _looks_like_header(row) or _is_end_row(row):
                    break
                if not any(_cells(row)):
                    continue
                items.append(
                    _build_item(row, cols, key, name, r, item_type, block_no)
                )
    return {"items": items, "sheet_count": sheet_count, "period": period}


def _build_item(row, cols, sheet_key: str, sheet_name: str, row_no: int,
                item_type: str, block_no: int) -> Dict:
    def cell(field: str) -> str:
        i = cols.get(field)
        if i is None or i >= len(row):
            return ""
        return str(row[i] or "").strip()

    share = cell("share_raw")
    salary = cell("salary_raw")
    partner = cell("partner_raw")
    tax = cell("tax_raw")
    fund = cell("fund_raw")
    net = cell("net_raw")
    return {
        "sheet_key": sheet_key,
        "sheet_name": sheet_name,
        "block_no": block_no,
        "row_no": row_no,
        "item_type": item_type,
        "seq": cell("seq"),
        "staff_name": _norm_name(cell("staff_name")),
        "share_raw": share, "share_num": _parse_num(share),
        "salary_raw": salary, "salary_num": _parse_num(salary),
        "partner_raw": partner, "partner_num": _parse_num(partner),
        "tax_raw": tax, "tax_num": _parse_num(tax),
        "fund_raw": fund, "fund_num": _parse_num(fund),
        "net_raw": net, "net_num": _parse_num(net),
        "pension_raw": cell("pension_raw"),
        "medical_raw": cell("medical_raw"),
        "unemployment_raw": cell("unemployment_raw"),
        "remark": "",
    }
