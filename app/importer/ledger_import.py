"""发票台账解析器（每月一本，4 个 sheet）

真实格式（2025.1-12 台账）：
- sheet 名跨月不统一，按关键词识别：已开票已入账 / 已开票未入账 / 应收账款 / 已入账未开票
- sheet1/2/3 列：序号|开票日期|发票号码|对方|金额|经办人|备注|案号
- sheet4 列：序号|收到日期|发票号码(空)|对方|金额|经办人|备注|案号
- sheet3（应收账款）：**全部行**移入 data["deferred"]（不再只移「期外」），只写镜表，
  由「补录原票」逐张确认；每行带 need_backfill 标记（票号不在库=需补录 /
  已在库=已入库请确认收款）。**绝不走普通导入了路径** —— 否则
  `importer._write_collection_for_invoice` 的 DELETE 会抹掉该票历史收款（D1 甲）。
  （方案 A 纯派生，见 app/engine/raw_ledger.deferred_sheet3_invoices）
- sheet3（应收账款）金额不允许为负：业务上应收账龄表只登记正数未收余额，
  出现负数即为台账填错 → 硬报错（问题行），修正后才可确认入库。
- sheet3（应收账款）开票月份须早于账期：出现 ≥ 账期（含当月）→ **硬报错中止导入**
  （A4 数据质量校验；见 is_period_before 的说明）。
- 开票日期解析失败 → 问题行（可在复核页修正后入库），**不再让整份台账导入失败**；
  空值放行（该行改由票号判定）。
"""
from __future__ import annotations

from typing import Dict, List

from app.engine.backfill import is_period_before
from app.importer.date_utils import normalize_date
from app.importer.excel_reader import ImportError_, cell_text, col_index, find_header_row, norm_header, read_sheet, sheet_names
from app.importer.parse_handler import parse_handler_column
from app.importer.parse_remark import parse_remark


def norm_full_date(text: str, default_year: int | None = None) -> str:
    """台账日期 → YYYY-MM-DD（委托通用 normalize_date，支持更多写法）。"""
    return normalize_date(text, default_year=default_year)


def _library_nos(in_library: set | None) -> set:
    """取「库中已有票号集合」；调用方已算好则复用（避免一次导入重复查库）。"""
    if in_library is not None:
        return in_library
    from app.engine.backfill import library_invoice_nos
    return library_invoice_nos()


def is_sheet3_row(inv: Dict) -> bool:
    """该行是否属于 sheet3（应收账款）—— sheet3 行一律不进普通导入路径。"""
    return (inv.get("sheet") or "") == "sheet3"


def is_deferred_invoice(inv: Dict, period: str, in_library: set | None = None) -> bool:
    """该行是否需要「补录原票」（= 移入 deferred 且需人工补录）。

    判据（2026-09-16 新口径）：`sheet3` 且 **票号不在库**。
    - sheet1/2 属本月销项、sheet4 是预收款 → 永不适用；
    - 票号已在库（销项已建票 / 已补录 / 历史自动建票）→ 不是「需补录」，而是
      「已入库，请确认收款」（D1 甲，由复核页确认后更新 collection）；
    - **不再看开票日期**：同一张历史应收会持续出现在各期 sheet3，按日期判「期外」
      会反复处理同一张票，且日期解析失败的行会被静默漏出清单。开票日期改由
      A4 数据质量校验（_parse_invoice_sheet）与复核页人工修正负责。

    period 保留仅为签名兼容（历史调用点传账期），判定不依赖它。
    in_library：库中已有票号集合；None 时现查 invoice 表。
    """
    if not is_sheet3_row(inv):
        return False
    no = (inv.get("invoice_no") or "").strip()
    if not no:
        return False
    return no not in _library_nos(in_library)


def split_deferred(data: Dict, period: str, in_library: set | None = None) -> int:
    """把 data["invoices"] 里 **全部 sheet3 行**移入 data["deferred"]，返回移出条数。

    为什么不再只移「期外」（2026-09-16 D1 甲）：应收账款里的老票（如 2024-11 开票、
    未收完款）会持续出现在各月 sheet3。若按票号判定后让它走普通票路径，
    `importer._write_collection_for_invoice` 会先
    `DELETE FROM collection WHERE invoice_no=? AND source='import'`
    → **抹掉该票历史收款**。故 sheet3 行一律移出：只写镜表，逐张确认后写入。

    每行打标记 `need_backfill`：
    - True  → 票号不在库 → 「需补录原票」
    - False → 票号已在库 → 「已入库，请确认收款」（不建票 / 不写分摊 / 不碰 collection）

    幂等：可重复调用（解析后、问题行修正合并后再各调一次），已在 deferred 的
    条目不会被重复追加或丢失。sheet_totals 保持「含 sheet3」的源口径不变。
    返回条数按「本次从 invoices 移出」计。
    """
    kept: List[Dict] = []
    moved: List[Dict] = []
    for inv in data.get("invoices") or []:
        (moved if is_sheet3_row(inv) else kept).append(inv)
    if moved:
        data["invoices"] = kept
        deferred = data.setdefault("deferred", [])
        seen = {d.get("invoice_no") for d in deferred}
        for inv in moved:
            if inv.get("invoice_no") not in seen:
                deferred.append(inv)
                seen.add(inv.get("invoice_no"))
    # 标记 deferred 桶里每一行（桶本身即 sheet3：只在移入时写入）。
    # 已有 deferred 时才算票号集合 —— 没有 sheet3 行就不必查库。
    deferred = data.get("deferred") or []
    if deferred:
        lib = _library_nos(in_library)
        for d in deferred:
            d["need_backfill"] = (d.get("invoice_no") or "").strip() not in lib
    return len(moved)


def _pick_sheet(rows: List[List[str]], keyword: str) -> List[List[str]] | None:
    """按关键词返回数据行（跳过表头）"""
    hr = find_header_row(rows, [keyword if keyword == "发票号码" else keyword[:4]])
    return rows


def classify_sheets(names: List[str]) -> Dict[str, str]:
    """把 sheet 名映射到类型：sheet1/sheet2/sheet3/sheet4（比较忽略空白）"""
    mapping: Dict[str, str] = {}
    for name in names:
        flat = norm_header(name)  # sheet 名也可能带排版空格，统一忽略空白
        if "已开票已入账" in flat:
            mapping["sheet1"] = name
        elif "已开票未入账" in flat:
            mapping["sheet2"] = name
        elif "应收账款" in flat:
            mapping["sheet3"] = name
        elif "已入账未开票" in flat:
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
            # C4：开票日期解析挪进 try —— 解析失败转「问题行」（复核页可修正后入库），
            # 不再让整份台账导入失败；**空值放行**（该行改由票号判定是否需补录）。
            date_text = g(row, idx_date)
            try:
                invoice_date = norm_full_date(date_text, year) if (idx_date >= 0 and date_text) else ""
            except ImportError_:
                raise ImportError_(
                    f"开票日期「{date_text}」无法识别（支持 2025.9.1 / 25.09.01 等写法）"
                ) from None
        except ImportError_ as e:
            problems.append(problem(str(e)))
            continue

        # ---- A4 数据质量校验：sheet3 开票月份必须早于账期 ----
        # 应收账款只登记**本账期之前**开票的发票。出现 ≥ 账期（含当月）的日期，必是台账
        # 填错（如把本月票误填进应收账款表）—— 此时按票号判定会把它当「需补录」而
        # 漏建本月票，故**中止导入**要求核对台账文件（D2：同账期 sheet3 与 sheet1/2 不会同号）。
        if sheet_key == "sheet3" and invoice_date:
            ym = invoice_date[:7]
            if not is_period_before(ym, period):
                raise ImportError_(
                    f"【{sheet_name}】第 {row_idx} 行发票 {no} 的开票日期 {invoice_date}"
                    f"（{ym}）不早于本账期 {period}。应收账款表只应登记本账期之前的发票，"
                    f"请核对台账文件后重新导入。"
                )

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
            "invoice_date": invoice_date,
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


def parse_ledger_file(path: str, period: str, in_library: set | None = None) -> Dict:
    """解析发票台账，返回结构化数据（未落库）

    返回: {invoices, deferred, prepayments, sheet_totals, problems, sheet12_total}
    problems 为无法解析的问题行（解析失败不中断，收集到此列表由上层处理）。
    deferred 为 sheet3（应收账款）**全部行**，不落 invoice，转入「补录原票」/「已入库确认收款」。
    in_library：库中已有票号集合（决定 deferred 行的 need_backfill 标记）；
    不传则现查 invoice 表。
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
            # sheet_totals 保持「源口径」：含 sheet3 行（即使它们随后被移入 deferred），
            # 便于与 Excel 原表逐 sheet 勾稽。
            result["sheet_totals"][key] = sum(i["total_amount"] for i in items)
        elif key == "sheet4":
            items, problems = _parse_sheet4(rows, name, period)
            result["prepayments"] = items
            result["problems"].extend(problems)

    result["sheet12_total"] = result["sheet_totals"].get("sheet1", 0.0) + result["sheet_totals"].get("sheet2", 0.0)
    # sheet3 行统一转入「补录原票」/「已入库确认收款」：见 split_deferred。
    # 放在最后统一切分，保证 validator / commit 看到的是切分后的集合。
    split_deferred(result, period, in_library)
    return result
