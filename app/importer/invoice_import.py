"""销项文档解析器（每月一本）

真实格式（销项导出2025.1.xlsx）：
- r1 标题行，r2 表头
- 列：序号 | 发票号码 | 发票种类 | 开票日期 | 发票状态 | 凭证号 | 货物、应税劳务及服务 | 购方名称 | 价税合计 | 不含税金额 | 税率 | 税额 | 备注
- 红字发票 = 价税合计为负（发票状态列全为"正常"）
- 红冲关联：备注"被红冲蓝字数电票号码：24332000000398937755 红字发票信息确认单编号：XXX"
"""
from __future__ import annotations

import re
from typing import Dict, List

from app.importer.date_utils import normalize_date
from app.importer.excel_reader import (
    ImportError_, col_index, find_header_row, read_sheet, sheet_names,
)

# 原始镜表列顺序（与 raw_invoice 表一一对应，不含 id/synced/import_batch_id/created_at）
RAW_FIELDS = [
    "sheet_name", "row_no", "seq", "invoice_no", "kind", "invoice_date_raw",
    "status", "voucher_no", "buyer", "total_amount_raw", "net_amount_raw",
    "tax_rate_raw", "tax_raw", "goods", "remark",
]

_RED_RE = re.compile(r"被红冲蓝字数电票号码[:：]\s*(\d+)")


def parse_invoice_file(path: str, period: str) -> List[Dict]:
    """解析销项文档，返回发票字典列表（未落库）"""
    rows = read_sheet(path, sheet_index=0)
    hr = find_header_row(rows, ["发票号码", "价税合计"])
    if hr < 0:
        raise ImportError_("销项文档未找到表头（需包含'发票号码'和'价税合计'列）")
    header = rows[hr]

    idx_no = col_index(header, "发票号码")
    idx_kind = col_index(header, "发票种类")
    idx_date = col_index(header, "开票日期")
    idx_status = col_index(header, "发票状态")
    idx_voucher = col_index(header, "凭证号")
    idx_goods = col_index(header, "货物", "应税")
    idx_buyer = col_index(header, "购方", "购买方")
    idx_total = col_index(header, "价税合计")
    idx_net = col_index(header, "不含税")
    idx_rate = col_index(header, "税率")
    idx_tax = col_index(header, "税额")
    idx_remark = col_index(header, "备注")

    invoices: List[Dict] = []
    seen = set()
    for row in rows[hr + 1:]:
        no = (row[idx_no].strip() if idx_no >= 0 and idx_no < len(row) else "")
        if not no:
            continue
        if no in seen:
            raise ImportError_(f"发票号码重复: {no}")
        seen.add(no)

        def g(i: int) -> str:
            return row[i].strip() if 0 <= i < len(row) else ""

        total_txt = g(idx_total)
        try:
            total = float(total_txt.replace(",", "")) if total_txt else 0.0
        except ValueError:
            raise ImportError_(f"发票 {no} 价税合计无法解析: 「{total_txt}」") from None

        remark = g(idx_remark)
        m = _RED_RE.search(remark)
        orig_no = m.group(1) if m else ""

        invoices.append({
            "invoice_no": no,
            "invoice_date": normalize_date(g(idx_date), default_year=int(period.split("-")[0])),
            "kind": g(idx_kind),
            "status": g(idx_status),
            "voucher_no": g(idx_voucher),
            "goods": g(idx_goods),
            "buyer": g(idx_buyer),
            "total_amount": total,
            "net_amount": float(g(idx_net)) if g(idx_net) else None,
            "tax_rate": g(idx_rate),
            "tax": float(g(idx_tax)) if g(idx_tax) else None,
            "remark": remark,
            "orig_invoice_no": orig_no,
            "is_red": total < 0,
            "period": period,
        })

    if not invoices:
        raise ImportError_("销项文档没有有效发票数据")

    # 红冲金额约束：原正数发票金额 + 对应负数发票金额 >= 0
    by_orig: Dict[str, float] = {}
    for inv in invoices:
        if inv["is_red"] and inv["orig_invoice_no"]:
            by_orig[inv["orig_invoice_no"]] = by_orig.get(inv["orig_invoice_no"], 0.0) + inv["total_amount"]
    for orig, s in by_orig.items():
        pos = next((i for i in invoices if i["invoice_no"] == orig), None)
        if pos:
            if pos["total_amount"] + s < -0.01:
                raise ImportError_(
                    f"红冲校验失败: 原票 {orig}({pos['total_amount']:g}) + 红字合计({s:g}) < 0"
                )
        else:
            # 原票不在本月销项（跨期红冲，合法）
            pass

    return invoices


def parse_invoice_workbook(path: str, period: str):
    """解析销项文档全部 sheet，返回 (raw_rows, warnings)。

    raw_rows：每个 sheet 的每一行原始数据（1:1 镜像，数值列原样存文本），供 raw_invoice 双写。
    - 列顺序见 RAW_FIELDS；sheet_name/row_no 记录来源用于溯源。
    - 无表头或表头不含「发票号码/价税合计」的 sheet 跳过并记入 warnings（不中断导入）。
    - 同一文件内发票号码重复仍报错（与单 sheet 行为一致）。
    调用方需自行处理 period 仅用于默认年份推断（原始日期文本原样保留，不归一化）。
    """
    warnings: List[str] = []
    try:
        names = sheet_names(path)
    except ImportError_ as e:
        raise ImportError_(f"读取 Excel sheet 失败: {e}") from None

    raw_rows: List[Dict] = []
    seen = set()
    for name in names:
        try:
            rows = read_sheet(path, sheet_name=name)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Sheet「{name}」读取失败，已跳过：{e}")
            continue
        hr = find_header_row(rows, ["发票号码", "价税合计"])
        if hr < 0:
            warnings.append(f"Sheet「{name}」未找到表头（需含「发票号码」「价税合计」），已跳过")
            continue

        header = rows[hr]
        idx_no = col_index(header, "发票号码")
        idx_seq = col_index(header, "序号")
        idx_kind = col_index(header, "发票种类")
        idx_date = col_index(header, "开票日期")
        idx_status = col_index(header, "发票状态")
        idx_voucher = col_index(header, "凭证号")
        idx_goods = col_index(header, "货物", "应税")
        idx_buyer = col_index(header, "购方", "购买方")
        idx_total = col_index(header, "价税合计")
        idx_net = col_index(header, "不含税")
        idx_rate = col_index(header, "税率")
        idx_tax = col_index(header, "税额")
        idx_remark = col_index(header, "备注")

        for i, row in enumerate(rows[hr + 1:]):
            def g(idx: int) -> str:
                return row[idx].strip() if 0 <= idx < len(row) else ""

            no = g(idx_no)
            if not no:
                continue
            if no in seen:
                raise ImportError_(f"发票号码重复: {no}（跨 sheet）")
            seen.add(no)

            raw_rows.append({
                "sheet_name": name,
                "row_no": i + 1,                         # 该 sheet 内行号（1 基）
                "seq": g(idx_seq),
                "invoice_no": no,
                "kind": g(idx_kind),
                "invoice_date_raw": g(idx_date),
                "status": g(idx_status),
                "voucher_no": g(idx_voucher),
                "buyer": g(idx_buyer),
                "total_amount_raw": g(idx_total),
                "net_amount_raw": g(idx_net),
                "tax_rate_raw": g(idx_rate),
                "tax_raw": g(idx_tax),
                "goods": g(idx_goods),
                "remark": g(idx_remark),
            })

    if not raw_rows:
        raise ImportError_("销项文档没有任何有效发票数据（全部 sheet 均无有效行）")
    return raw_rows, warnings
