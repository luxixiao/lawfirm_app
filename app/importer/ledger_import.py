"""发票台账解析器（每月一本，4 个 sheet）

真实格式（2025.1-12 台账）：
- sheet 名跨月不统一，按关键词识别：已开票已入账 / 已开票未入账 / 应收账款 / 已入账未开票
- sheet1/2/3 列：序号|开票日期|发票号码|对方|金额|经办人|备注|案号
- sheet4 列：序号|收到日期|发票号码(空)|对方|金额|经办人|备注|案号
- sheet3（应收账款）含期外发票（开票日期早于导入月份）：**不再自动建票**，
  由 split_deferred 移入 data["deferred"]，只写镜表并入「补录原票」逐张确认
  （方案 A 纯派生，见 app/engine/raw_ledger.deferred_sheet3_invoices）。
- sheet3（应收账款）金额不允许为负：业务上应收账龄表只登记正数未收余额，
  出现负数即为台账填错 → 硬报错（问题行），修正后才可确认入库。
"""
from __future__ import annotations

from typing import Dict, List

from app.engine.backfill import is_period_before
from app.importer.date_utils import normalize_date
from app.importer.excel_reader import ImportError_, cell_text, col_index, find_header_row, read_sheet, sheet_names
from app.importer.parse_handler import parse_handler_column
from app.importer.parse_remark import parse_remark


def norm_full_date(text: str, default_year: int | None = None) -> str:
    """台账日期 → YYYY-MM-DD（委托通用 normalize_date，支持更多写法）。"""
    return normalize_date(text, default_year=default_year)


def is_deferred_invoice(inv: Dict, period: str) -> bool:
    """该发票是否应转入「补录原票」（sheet3 期外，口径 a：开票月份 < 账期月份）。

    只有 sheet3（应收账款）适用：sheet1/2 属本月销项、sheet4 是预收款。
    日期缺失/无法解析时**不转**（判定期外的前提是拿到开票月份），保持原行为。
    """
    if (inv.get("sheet") or "") != "sheet3":
        return False
    return is_period_before((inv.get("invoice_date") or "")[:7], period)


def split_deferred(data: Dict, period: str) -> int:
    """把 data["invoices"] 里应转补录的行移入 data["deferred"]，返回移出条数。

    幂等：可重复调用（解析后、问题行修正合并后再各调一次），已在 deferred 的
    条目不会被重复追加或丢失。sheet_totals 保持「含期外」的源口径不变。
    """
    kept: List[Dict] = []
    moved: List[Dict] = []
    for inv in data.get("invoices") or []:
        (moved if is_deferred_invoice(inv, period) else kept).append(inv)
    if not moved:
        return 0
    data["invoices"] = kept
    deferred = data.setdefault("deferred", [])
    seen = {d.get("invoice_no") for d in deferred}
    for inv in moved:
        if inv.get("invoice_no") not in seen:
            deferred.append(inv)
            seen.add(inv.get("invoice_no"))
    return len(moved)


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


def _parse_invoice_sheet(rows: List[List[str]], sheet_key: str, sheet_name: str, period: str) -> "tuple[List[Dict], List[Dict]]":
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
                # 保留原始台账行：修正界面可「查看原始台账行」对照填写（与解析成功行一致）
                "header": header, "raw_row": list(row),
            }

        amt_txt = g(row, idx_amt)
        try:
            total = float(amt_txt.replace(",", "")) if amt_txt else 0.0
        except ValueError:
            problems.append(problem(f"金额无法解析「{amt_txt}」"))
            continue

        # sheet3（应收账款）业务上不可能出现负数：红冲/退款走 sheet1/2 与退款台账，
        # 应收账龄表只登记正数未收余额。负数说明台账填错 → 硬报错（问题行），
        # 在复核页修正金额为正数后才可确认入库；未修正则在确认时被跳过（不入库）。
        if sheet_key == "sheet3" and total < 0:
            problems.append(problem(f"应收账款金额不能为负数（{total:g}），请修正台账后重新导入"))
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

        # ---- 已收/未收按 sheet 归属修正（sheet 是权威，备注为辅）----
        # sheet2（已开票未入账）：纯日期备注不是收款证据（单日期是挂账/应收信息，如 25.3.4），
        #   降级为未收；有明确收款动词（收/汇/到）才收。
        # sheet3（应收账款）：纯日期备注 = 该历史应收的【收款日期】，按全额收款处理
        #   （如 25.2.28 表示此笔应收在 2025-02 收回）；故不再清空，交由下方 importer 写收款。
        if sheet_key == "sheet2" and remark.get("pure_date"):
            remark["pure_date"] = None
            remark["receipts"] = []
        # sheet1（已开票已入账）：备注无任何收款明细（空备注/旧写法）→ 补全额收款兜底，
        # 保证「已开已收」的票一定判已收（日期取导入账期月）。
        if sheet_key == "sheet1" and not remark["receipts"] and total >= 0:
            remark["receipts"] = [(period, 0.0)]  # 0 = 全额（importer 写入时填开票总额）

        items.append({
            "sheet": sheet_key,
            "sheet_name": sheet_name,
            "row_no": row_idx,
            "header": header,
            "raw_row": list(row),
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


def _parse_sheet4(rows: List[List[str]], sheet_name: str, period: str) -> "tuple[List[Dict], List[Dict]]":
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
                # 保留原始台账行：修正界面可「查看原始台账行」对照填写
                "header": header, "raw_row": list(row),
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
            "sheet": "sheet4",
            "sheet_name": sheet_name,
            "row_no": row_idx,
            "header": header,
            "raw_row": list(row),
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

    返回: {invoices, deferred, prepayments, sheet_totals, problems, sheet12_total}
    problems 为无法解析的问题行（解析失败不中断，收集到此列表由上层处理）。
    deferred 为 sheet3 期外票（开票月份 < 账期月份），不落 invoice，转入「补录原票」。
    """
    names = sheet_names(path)
    mapping = classify_sheets(names)
    if not mapping:
        raise ImportError_("未识别到发票台账 sheet（需含'已开票已入账/已开票未入账/应收账款/已入账未开票'）")

    result: Dict = {"invoices": [], "deferred": [], "prepayments": [],
                    "sheet_totals": {}, "problems": []}
    for key, name in mapping.items():
        rows = read_sheet(path, sheet_name=name)
        if key in ("sheet1", "sheet2", "sheet3"):
            items, problems = _parse_invoice_sheet(rows, key, name, period)
            result["invoices"].extend(items)
            result["problems"].extend(problems)
            # sheet_totals 保持「源口径」：含期外行（即使它们随后被移入 deferred），
            # 便于与 Excel 原表逐 sheet 勾稽。
            result["sheet_totals"][key] = sum(i["total_amount"] for i in items)
        elif key == "sheet4":
            items, problems = _parse_sheet4(rows, name, period)
            result["prepayments"] = items
            result["problems"].extend(problems)

    result["sheet12_total"] = result["sheet_totals"].get("sheet1", 0.0) + result["sheet_totals"].get("sheet2", 0.0)
    # sheet3 期外票转入「补录原票」：不落 invoice，由补录流程逐张确认。
    # 放在最后统一切分，保证 validator / commit 看到的是切分后的集合。
    split_deferred(result, period)
    return result
