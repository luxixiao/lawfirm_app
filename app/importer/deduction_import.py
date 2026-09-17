"""费用扣除表解析（1-12 月，单层表头，无税局字段编号行）

源文件特征（如 2025费用扣除.xlsx = 2025 年 1-12 月费用扣除）：
- **单层表头**（R0 一行到底，不像个税申报表那样三层合并）：
  姓名 / 累计减除费用 / 累计专项扣除 / 累计专项* / 累计子女教育支出扣除 /
  累计继续教育支出扣除 / 累计住房贷款利息支出扣除 / 累计住房租金支出扣除 /
  累计赡养老人支出扣除 / 累计3岁以下婴幼儿照护 / 累计个人养老金 / 累计其他扣除 /
  累计准予扣除的捐赠 / 其他单位累计收入 / 其他单位累计扣除 /
  其他单位累计减免税额 / 其他单位累计减免税额
  · 第 3 列「累计专项」是表头被截断，实为「累计专项附加扣除」合计列
  · 末两列同名重复（源表如此）
- 数据自表头下一行开始；本表无合计行。

应对「税局表格每年变化」（本表没有编号行，故以列名为锚）：
1. **列名关键词匹配**：用「包含匹配」而非精确匹配，容忍列名微调
   （如「累计子女教育支出扣除」<->「子女教育」都能命中）；
2. 匹配不上 -> 存入 extra（页面动态追加为附加列），**绝不因多一列而失败**；
3. 缺列 -> 该字段留空，**绝不因少一列而失败**；
4. 重名列 -> 第二个起加「(2)」后缀，不互相覆盖。
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Tuple

from app.importer.excel_reader import norm_header, read_sheet, sheet_names

# (关键词元组, 字段) —— 列名需**全部包含**这些关键词才命中；按顺序先到先得。
# 顺序要点：先具体后宽泛（「专项扣除」必须在「专项」之前，否则会被宽泛规则抢走）。
FIELD_RULES: List[Tuple[Tuple[str, ...], str]] = [
    (("姓名",), "staff_name"),
    (("减除费用",), "basic_deduction"),
    (("专项扣除",), "special_deduction"),
    (("专项附加",), "additional_total"),
    (("专项",), "additional_total"),          # 表头截断为「累计专项」时的兜底
    (("子女教育",), "child_edu"),
    (("继续教育",), "education"),
    (("住房贷款",), "housing_loan"),
    (("住房租金",), "housing_rent"),
    (("赡养老人",), "elderly"),
    (("婴幼儿",), "infant"),
    (("个人养老金",), "pension"),
    (("捐赠",), "donation"),
    (("其他扣除",), "other_deduction"),
    (("其他单位", "收入"), "other_income"),
    (("其他单位", "扣除"), "other_deduct"),
    (("其他单位", "减免"), "other_relief"),
]

STD_FIELDS = [
    "staff_name", "basic_deduction", "special_deduction", "additional_total",
    "child_edu", "education", "housing_loan", "housing_rent", "elderly",
    "infant", "pension", "other_deduction", "donation",
    "other_income", "other_deduct", "other_relief",
]


def guess_year(filename: str) -> str:
    """从文件名猜年份（取第一个 4 位数字）：2025费用扣除.xlsx -> 2025。"""
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


def _uniq_names(names: List[str]) -> List[str]:
    """重名列加序号后缀（源表末两列同名「其他单位累计减免税额」）。"""
    seen: Dict[str, int] = {}
    out: List[str] = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}({seen[n]})")
        else:
            seen[n] = 1
            out.append(n)
    return out


def _find_header_row(rows: List[List[str]]) -> int:
    """表头行 = 第一个含「姓名」的行（表头比较**忽略空白**）。"""
    want = norm_header("姓名")
    for i, row in enumerate(rows):
        if any(norm_header(c) == want for c in _cells(row)):
            return i
    return -1


def _build_col_map(names: List[str]):
    """返回 (字段映射 {列索引: 字段}, 附加列 {列索引: 列名})。

    匹配前把列名**去空白**（`norm_header`）：台账表头常带排版空格（如「应 发 工 资」），
    不去空白会整列漏掉；`extra` 仍回填**原始列名**，保持界面显示不变。
    """
    mapped: Dict[int, str] = {}
    used = set()
    for c, name in enumerate(names):
        if not name:
            continue
        flat = norm_header(name)
        for keys, field in FIELD_RULES:
            if all(norm_header(k) in flat for k in keys) and field not in used:
                mapped[c] = field
                used.add(field)
                break
    extra = {c: names[c] for c in range(len(names)) if c not in mapped and names[c]}
    return mapped, extra


def parse_deduction_file(path: str) -> Dict:
    """解析费用扣除表。

    返回 {"items": [...], "extra_cols": [附加列名...]}
    items 每行字段与 tax_deduction 列对应（year 由调用方填写）。
    """
    name = sheet_names(path)[0] if sheet_names(path) else None
    rows = read_sheet(path, sheet_name=name) if name else read_sheet(path)
    if not rows:
        return {"items": [], "extra_cols": []}

    ncols = max(len(r) for r in rows)
    header_row = _find_header_row(rows)
    if header_row < 0:
        return {"items": [], "extra_cols": []}

    names = _uniq_names(_cells(rows[header_row])[:ncols])
    mapped, extra = _build_col_map(names)

    items: List[Dict] = []
    for r in range(header_row + 1, len(rows)):
        cells = _cells(rows[r])
        if not any(cells):
            continue
        first = re.sub(r"\s+", "", cells[0])
        if "合计" in first or "总计" in first:
            break
        item: Dict = {f: ("" if f == "staff_name" else 0.0) for f in STD_FIELDS}
        extra_vals: Dict[str, str] = {}
        for c in range(ncols):
            v = cells[c] if c < len(cells) else ""
            if c in mapped:
                f = mapped[c]
                item[f] = v if f == "staff_name" else _num(v)
            elif c in extra and v:
                extra_vals[extra[c]] = v
        if not item.get("staff_name"):
            continue
        item["extra_json"] = json.dumps(extra_vals, ensure_ascii=False) if extra_vals else ""
        items.append(item)

    return {"items": items, "extra_cols": sorted({extra[c] for c in extra})}
