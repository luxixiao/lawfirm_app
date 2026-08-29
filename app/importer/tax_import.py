"""个税申报表解析（税局导出：三层合并表头 + 字段编号行）

源文件特征（如 20251-11个税申报情况.xlsx = 2025 年 1-11 月累计）：
- 三层合并表头：
    R0 一级：序号 / 姓名 / 累计情况(合并) / 税款计算(合并)
    R1 二级：累计收入额 / 累计减除费用 / 累计专项扣除 / 累计专项附加扣除(合并) /
             应纳税所得额 / 税率 / 速算扣除数 / 应纳税额 / 减免税额 / 已缴税额 / 应补退税额
    R2 三级：子女教育 / 赡养老人 / 住房贷款利息 / 住房租金 / 继续教育 /
             3岁以下婴幼儿照护 / 实际已纳税额
- **R3 为税局字段编号行**：1 2 22 23 24 25 26 27 28 29 30 35 36 37 38 39 40 41
- 数据自编号行下一行开始；末尾「合　计」行为合计，需跳过。

应对「税局表格每年变化」的策略（核心）：
1. **优先按字段编号映射**——编号体系稳定，改名 / 换序 / 增删列都不影响核心字段取值；
2. 无编号的列（如「实际已纳税额」）→ 用**最深层列名**兜底匹配；
3. 两者都匹配不上 → 存入 extra（页面作为附加列显示），**绝不因多一列而失败**；
4. 缺列 → 该字段留空，**绝不因少一列而失败**。
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from app.importer.excel_reader import read_sheet, sheet_names

# 税局字段编号 -> 标准字段（编号体系稳定，是本解析器的主锚点）
FIELD_BY_NO = {
    "1": "seq", "2": "staff_name",
    "22": "income", "23": "basic_deduction", "24": "special_deduction",
    "25": "child_edu", "26": "elderly", "27": "housing_loan",
    "28": "housing_rent", "29": "education", "30": "infant",
    "35": "taxable", "36": "tax_rate", "37": "quick_ded",
    "38": "payable", "39": "relief", "40": "paid", "41": "refill",
}

# 列名兜底（用于无编号列，如「实际已纳税额」）
FIELD_BY_NAME = {
    "序号": "seq", "姓名": "staff_name",
    "累计收入额": "income", "累计减除费用": "basic_deduction",
    "累计专项扣除": "special_deduction",
    "子女教育": "child_edu", "赡养老人": "elderly",
    "住房贷款利息": "housing_loan", "住房租金": "housing_rent",
    "继续教育": "education", "3岁以下婴幼儿照护": "infant",
    "应纳税所得额": "taxable", "税率/预扣率": "tax_rate",
    "速算扣除数": "quick_ded", "应纳税额": "payable",
    "减免税额": "relief", "已缴税额": "paid",
    "应补/退税额": "refill", "实际已纳税额": "net_paid",
}

# 这些字段按原文存储（不转数值：序号可能带前导零、税率可能是 "-"）
TEXT_FIELDS = {"seq", "staff_name", "tax_rate"}

# 除 extra/文件名/时间外的全部标准字段
STD_FIELDS = [
    "seq", "staff_name", "income", "basic_deduction", "special_deduction",
    "child_edu", "elderly", "housing_loan", "housing_rent", "education",
    "infant", "taxable", "tax_rate", "quick_ded", "payable", "relief",
    "paid", "refill", "net_paid",
]


def guess_year(filename: str) -> str:
    """从文件名猜申报年份（取第一个 4 位数字）：20251-11… -> 2025。"""
    m = re.search(r"(\d{4})", filename)
    return m.group(1) if m else ""


def _num(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
    if not m:
        return 0.0
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return 0.0


def _cells(row) -> List[str]:
    return [str(c).strip() if c is not None else "" for c in row]


def _find_header_row(rows: List[List[str]]) -> int:
    """表头首行 = 第一个含「姓名」的行。"""
    for i, row in enumerate(rows):
        if any(c == "姓名" for c in _cells(row)):
            return i
    return -1


def _find_no_row(rows: List[List[str]], header_row: int) -> int:
    """表头之后第一个「≥5 个单元格是纯整数」的行 = 税局字段编号行。"""
    for r in range(header_row + 1, min(header_row + 6, len(rows))):
        nums = sum(1 for c in _cells(rows[r]) if re.fullmatch(r"\d{1,3}", c))
        if nums >= 5:
            return r
    return -1


def _col_names(rows: List[List[str]], header_row: int, ncols: int) -> List[str]:
    """每列取**最深层**的非空表头文字（合并表头下，最深一层才是真实字段名）。"""
    names: List[str] = []
    for c in range(ncols):
        name = ""
        for r in range(header_row, min(header_row + 3, len(rows))):
            v = _cells(rows[r])
            if c < len(v) and v[c]:
                name = v[c]
        names.append(name)
    return names


def _build_col_map(rows, header_row: int, no_row: int, ncols: int):
    """返回 (字段映射 {列索引: 标准字段}, 附加列 {列索引: 列名})。"""
    names = _col_names(rows, header_row, ncols)
    mapped: Dict[int, str] = {}
    used = set()
    if no_row >= 0:
        no_cells = _cells(rows[no_row])
        for c in range(min(ncols, len(no_cells))):
            field = FIELD_BY_NO.get(no_cells[c])
            if field and field not in used:
                mapped[c] = field
                used.add(field)
    # 未覆盖的列：按列名兜底
    for c, name in enumerate(names):
        if c in mapped or not name:
            continue
        field = FIELD_BY_NAME.get(name)
        if field and field not in used:
            mapped[c] = field
            used.add(field)
    # 仍未识别的列 -> 附加列（页面动态展示，不丢数据）
    extra = {c: (names[c] or f"列{c + 1}") for c in range(ncols) if c not in mapped}
    return mapped, extra


def parse_tax_file(path: str) -> Dict:
    """解析个税申报表。

    返回 {"items": [...], "extra_cols": [附加列名...], "year_hint": 文件名猜的年份}
    items 每行字段与 tax_declaration 列对应（year 由调用方填写）。
    """
    name = sheet_names(path)[0] if sheet_names(path) else None
    rows = read_sheet(path, sheet_name=name) if name else read_sheet(path)
    if not rows:
        return {"items": [], "extra_cols": [], "year_hint": ""}

    ncols = max(len(r) for r in rows)
    header_row = _find_header_row(rows)
    if header_row < 0:
        return {"items": [], "extra_cols": [], "year_hint": ""}
    no_row = _find_no_row(rows, header_row)
    data_start = (no_row + 1) if no_row >= 0 else (header_row + 3)

    mapped, extra = _build_col_map(rows, header_row, no_row, ncols)
    items: List[Dict] = []
    for r in range(data_start, len(rows)):
        cells = _cells(rows[r])
        if not any(cells):
            continue
        # 合计行首格形如「合　  计」（空白字符不定），统一去掉所有空白再判断
        first = re.sub(r"\s+", "", cells[0])
        if "合计" in first or "总计" in first:
            break
        item: Dict = {f: ("" if f in TEXT_FIELDS else 0.0) for f in STD_FIELDS}
        extra_vals: Dict[str, str] = {}
        for c in range(ncols):
            v = cells[c] if c < len(cells) else ""
            if c in mapped:
                f = mapped[c]
                item[f] = v if f in TEXT_FIELDS else _num(v)
            elif c in extra and v:
                extra_vals[extra[c]] = v
        if not (item.get("staff_name") or item.get("seq")):
            continue
        item["extra_json"] = json.dumps(extra_vals, ensure_ascii=False) if extra_vals else ""
        items.append(item)

    return {
        "items": items,
        "extra_cols": sorted({extra[c] for c in extra}),
        "year_hint": "",
    }
