"""发票台账原始镜表引擎

- raw_ledger 是发票台账文档 Excel 逐 sheet 逐行 1:1 镜像（覆盖 sheet1~4 全部原始列），
  与归一化 invoice 表解耦。导入双写落库，可手工编辑。
- 修改记录：所有对 raw_ledger 的编辑写入 change_log（统一审计中心数据源之一）。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from app.db import get_conn
from app.engine.change_log import log_change, build_friendly_table

# 可编辑字段（编辑弹窗用）
EDIT_FIELDS = [
    "seq", "invoice_date_raw", "invoice_no", "buyer",
    "amount_raw", "handler_text", "remark", "case_no", "recv_date_raw",
]

# sheet 展示顺序
SHEET_ORDER = ["sheet1", "sheet2", "sheet3", "sheet4"]

# sheet 展示名（用于「发票台账」页的工作表筛选与来源列，避免显示 Excel 原始名
# 含账期前缀的怪名，如 "202502已开票已入账" / "2025已入账未开票"）
SHEET_LABELS = {
    "sheet1": "已开票已入账",
    "sheet2": "已开票未入账",
    "sheet3": "应收账款",
    "sheet4": "已入账未开票",
}


def sheet_label(key: str) -> str:
    """sheet_key -> 干净展示名；未知/空返回原值。"""
    return SHEET_LABELS.get(key, key or "")


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def sheet_keys() -> List[Dict]:
    """返回当前库中存在的 sheet（按固定顺序），含干净的展示名。

    展示名取自 SHEET_LABELS（已开票已入账等），不再使用 Excel 原始工作表名
    （原始名常带账期前缀且格式不统一，如 "202502已开票已入账"）。
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT sheet_key FROM raw_ledger "
            "WHERE import_batch_id IS NOT NULL AND sheet_key IS NOT NULL AND sheet_key != ''"
        ).fetchall()
    finally:
        conn.close()
    present = {r["sheet_key"] for r in rows}
    out = []
    for k in SHEET_ORDER:
        if k in present:
            out.append({"sheet_key": k, "sheet_name": SHEET_LABELS.get(k, k)})
    return out


def list_raw(sheet_key: str = "", keyword: str = "", status: str = "",
             imported_only: bool = False, with_period: bool = True) -> List[Dict]:
    """返回 raw_ledger 行（按 sheet/row 排序）。

    sheet_key: ""=全部；否则按 sheet1~4 过滤。
    status:    ""=全部；"red"=仅红字（金额<0）。
    keyword:   匹配 发票号码/对方/金额文本/案号（LIKE，不区分大小写）。
    imported_only: 仅来自导入的行（import_batch_id 非空）。
    with_period:  LEFT JOIN import_batch 带出 period（导入账期，如 "2025-01"），
                  用于「发票台账」页按台账时间（导入账期）筛选，而非按发票自身日期。
    """
    clauses, params = [], []
    if sheet_key:
        clauses.append("l.sheet_key = ?")
        params.append(sheet_key)
    if imported_only:
        clauses.append("l.import_batch_id IS NOT NULL")
    if keyword:
        clauses.append("(l.invoice_no LIKE ? OR l.buyer LIKE ? OR l.amount_raw LIKE ? OR l.case_no LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    join = " LEFT JOIN import_batch b ON b.id = l.import_batch_id" if with_period else ""
    sel = "l.*" + (", b.period AS period" if with_period else "")
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT {sel} FROM raw_ledger l{join}{where} ORDER BY l.sheet_key, l.row_no",
            params,
        ).fetchall()
    finally:
        conn.close()
    out = [dict(r) for r in rows]
    if status == "red":
        out = [r for r in out if _parse_total(r.get("amount_raw")) < 0]
    return out


def deferred_sheet3_invoices(period: Optional[str] = None,
                             batch_id: Optional[int] = None,
                             conn=None) -> List[Dict]:
    """应收账款(sheet3) 中「**invoice 表缺号**」的行 → 待补录（源 B）。

    判据**只看票号**（2026-09-16 新口径，见 app.engine.backfill.library_invoice_nos）：
    票号不在 invoice 表 → 需补录原票。**不再按开票月份判「期外」** —— 同一张历史应收会
    持续出现在各期 sheet3，按日期判会反复处理同一张票；且开票日期解析失败的行会被
    静默漏出清单（`_norm_date_text` 失败回 ""，而 `is_period_before("", period)` 恒为 False）。
    与导入期切分（app.importer.ledger_import.is_deferred_invoice）**同源**，避免判定漂移。

    这些行按方案 A 只写 raw_ledger 镜像、**不落 invoice** —— 它们是历史应收，
    原票并未随任何一期销项/台账文档入库，必须由「补录原票」逐张确认后写入。

    period / batch_id：限定账期或导入批次（复核页打「需补录」标记用）；均不传则全库扫描。
    conn：传入则复用外部连接（单事务/测试注入用），不传自开自关。
    返回（按账期、行号排序；同票号取末行）每项含补录弹窗可直接使用的预填：
    invoice_no / invoice_date / buyer / total_amount / handlers
    + receipt_items（备注推导的收款批次，供列表展示「收款金额」）。
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        sql = (
            "SELECT l.id, l.import_batch_id, l.sheet_name, l.row_no, l.invoice_no, l.buyer, "
            "l.invoice_date_raw, l.amount_num, l.handler_text, l.remark, l.case_no, "
            "b.period AS period "
            "FROM raw_ledger l JOIN import_batch b ON b.id = l.import_batch_id "
            "WHERE l.kind='invoice' AND l.sheet_key='sheet3' AND b.status='active'"
        )
        params: List = []
        if period:
            sql += " AND b.period = ?"
            params.append(period)
        if batch_id is not None:
            sql += " AND l.import_batch_id = ?"
            params.append(batch_id)
        sql += " ORDER BY b.period, l.id"
        rows = conn.execute(sql, params).fetchall()
        out: Dict[str, Dict] = {}
        order: List[str] = []
        for r in rows:
            no = (r["invoice_no"] or "").strip()
            if not no:
                continue
            year = _period_year(r["period"])
            date_str = _norm_date_text(r["invoice_date_raw"], year)
            if conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (no,)).fetchone():
                continue  # 已入库（销项已建 / 已补录）→ 不再待补录（唯一判据，不看日期）
            if no not in out:
                order.append(no)
            out[no] = _derive_deferred(r, date_str, year)
        return [out[no] for no in order]
    finally:
        if own:
            conn.close()


def deferred_sheet3_nos(period: str, batch_id: Optional[int] = None,
                        conn=None) -> set:
    """本账期（可选限定批次）待补录的 sheet3 缺号票号集合（见 deferred_sheet3_invoices）。

    供复核页给镜表行打 `needs_backfill` 标记用（行仍保留在差异表里，只是以
    「需补录」待办呈现、不计入差异统计）。
    """
    return {d["invoice_no"] for d in deferred_sheet3_invoices(period, batch_id, conn)}


def _period_year(period: Optional[str]):
    try:
        return int(str(period)[:4])
    except (ValueError, TypeError):
        return None


def _norm_date_text(raw: str, year) -> str:
    """台账原始日期文本 → YYYY-MM-DD；解析失败回空串。"""
    from app.importer.date_utils import normalize_date
    try:
        return normalize_date(raw, default_year=year) or ""
    except Exception:  # noqa: BLE001 解析失败按「无法判定期外」处理
        return ""


def expand_receipts(handlers, remark, total) -> "tuple[List[Dict], List[tuple]]":
    """(经办人分摊, 备注, 价税合计) → 预填的「期 × 经办人」行 + 收款明细。

    返回 (rows, split_receipts)：
    - rows: [{"name", "billing", "received", "date"}] —— 每人**第一行**填开票分摊额、
      其余行填 0（保证「开票额合计 = 开票总额」校验通过，且
      `problem_fix_panel._aggregate_handlers` 合并后各人分摊正确）；
    - split_receipts: [(name, received, ym)]，只含 received>0，与 rows 中「有收款」的行
      一一对应（两条通道口径一致）。

    口径（D3 甲 / A11）：
    - 备注纯日期 → 该笔应收的收款日期、全额；备注逐期收款 → 各期金额（0 表示全额）；
    - **同月多笔先按 `ym` 合并**（明细粒度只到月，合并无损），避免同一人同一月
      展开出两行同日期的收款；
    - 无收款信息（备注既无纯日期也无逐期明细）→ 每人一行、收款留空
      （台账没表态就不预填，而不是臆造收款）。

    与复核页应收账款行的预填**同源**：`_derive_deferred` 与
    `unified_import_dialog` 都走这里，避免「补录页」与「复核页」两处口径漂移。
    """
    items = _receipt_periods(remark, total)
    rows: List[Dict] = []
    for name, billing in (handlers or []):
        share = (billing / total) if total else 0.0
        if not items:
            rows.append({"name": name, "billing": billing,
                         "received": 0.0, "date": ""})
            continue
        for k, (ym, amt) in enumerate(items):
            recv = round(amt * share, 2)
            has = recv > 0.001
            rows.append({
                "name": name,
                "billing": (billing if k == 0 else 0.0),
                "received": recv if has else 0.0,
                "date": (ym or "") if has else "",
            })
    split = [(h["name"], h["received"], h["date"]) for h in rows if h["received"] > 0.001]
    return rows, split


def _receipt_periods(remark, total) -> List[tuple]:
    """备注 → [(ym, amount)]（同月已合并）；无收款信息返回 []。

    - 纯日期备注 → [(该日期所在月, 全额)]（应收账款纯日期 = 该笔应收的收款日期）；
    - 逐期备注 → 各期金额（0 表示全额）；
    - 同月多笔合并（粒度只到月，无损）。
    金额一律是**绝对金额**；`expand_receipts`（预填行）与 `_derive_deferred` 的
    `receipt_items`（列表页「收款金额」）共用这一份取值口径。
    """
    items: List = []
    if total >= 0:
        if remark.get("pure_date"):
            items = [((remark["pure_date"] or "")[:7], total)]
        elif remark.get("receipts"):
            items = [((ym or "")[:7], (amt if amt > 0 else total))
                     for ym, amt in remark["receipts"]]
    merged: List = []
    for ym, amt in items:
        if merged and merged[-1][0] == ym:
            merged[-1] = (ym, merged[-1][1] + amt)
        else:
            merged.append((ym, amt))
    return merged


def _derive_deferred(r, date_str: str, year) -> Dict:
    """raw_ledger 行 → 待补录条目（含补录弹窗预填）。"""
    from app.importer.parse_handler import parse_handler_column
    from app.importer.parse_remark import parse_remark

    total = r["amount_num"] or 0.0
    no = (r["invoice_no"] or "").strip()
    try:
        hs = parse_handler_column(r["handler_text"] or "", total, no)
    except Exception:  # noqa: BLE001 与导入期口径一致：解析失败按空分摊
        hs = []
    try:
        rem = parse_remark(r["remark"], default_year=year)
    except Exception:  # noqa: BLE001
        rem = {}
    handlers, split_receipts = expand_receipts(hs, rem, total)
    items = _receipt_periods(rem, total)
    return {
        "invoice_no": no,
        "source": "应收账款",
        "raw_id": r["id"],
        "period": r["period"] or "",
        "sheet_name": r["sheet_name"] or "",
        "row_no": r["row_no"] or 0,
        "invoice_date": date_str,
        "buyer": r["buyer"] or "",
        # 价税合计原样带出（含红字负数）：不替用户改符号，由其在补录弹窗中确认。
        "total_amount": total,
        "handler_text": r["handler_text"] or "",
        "remark": r["remark"] or "",
        "handlers": handlers,
        # 预填收款（经办人 × 期）——复核页「确认（不改）」即采纳此集合；
        # 与 handlers 中 received>0 的行一一对应，保证两条通道口径一致。
        "split_receipts": split_receipts,
        "receipt_items": [{"ym": ym, "amount": amt} for ym, amt in items],
        "collected": round(sum(a for _, a in items), 2),
    }


def get_row(rid: int, conn=None) -> Optional[Dict]:
    """读一行 raw_ledger。conn 缺省自开自关；传入则复用外部连接（单事务用）。"""
    owns = conn is None
    if owns:
        conn = get_conn()
    try:
        r = conn.execute("SELECT * FROM raw_ledger WHERE id=?", (rid,)).fetchone()
    finally:
        if owns:
            conn.close()
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# 写：编辑（手动修改能力，自动写审计）
# ---------------------------------------------------------------------------
def update_row(rid: int, data: Dict, note: str = "", conn=None) -> list:
    """编辑一行 raw_ledger，逐字段比对并写 change_log（仅记录变了的字段）。

    关键修复：编辑发票台账行后，向下游归一化表同步，使「发票收款情况」
    （读 invoice 表）与「经办人发票收款情况」（读 charge_detail）能够刷新。
    仅同步真正变更的字段，避免用镜表原始文本覆盖进项文档导入时已规范化的字段。

    返回本次变更的字段名列表（供回写引擎做 L2 同步汇总）；无变更返回空列表。
    conn 缺省时自开连接、自 commit/close（发票台账页旧路径不受影响）；
    传入 conn 时复用外部连接且不 commit/close（导入复核回写引擎需单事务，见
    app/engine/review_writeback.apply_edit）。
    """
    old = get_row(rid, conn)
    if old is None:
        return []
    changes = []
    new_vals = []
    for f in EDIT_FIELDS:
        o = old.get(f) or ""
        n = data.get(f) or ""
        if str(o) != str(n):
            changes.append((f, o, n))
            new_vals.append(n)
        else:
            new_vals.append(o)
    if not changes:
        return []
    owns = conn is None
    if owns:
        conn = get_conn()
    try:
        sets = ", ".join(f"{f}=?" for f in EDIT_FIELDS)
        conn.execute(f"UPDATE raw_ledger SET {sets} WHERE id=?", new_vals + [rid])
        # 金额文本变化同步更新数值列
        if "amount_raw" in (c[0] for c in changes):
            amt = data.get("amount_raw") or ""
            try:
                conn.execute("UPDATE raw_ledger SET amount_num=? WHERE id=?",
                             (_parse_total(amt), rid))
            except ValueError:
                pass
        # 修改前整行快照（供修改记录页展示发票号码/对方/金额/经办人/修改表名）
        ctx = dict(
            friendly_table=build_friendly_table("raw_ledger", old.get("sheet_name") or ""),
            invoice_no=(old.get("invoice_no") or "").strip(),
            buyer=(old.get("buyer") or "").strip(),
            amount=(old.get("amount_raw") or "").strip(),
            handlers=(old.get("handler_text") or "").strip(),
        )
        log_change(conn, "raw_ledger", str(rid), "edit", "", "", note, **ctx)
        for f, o, n in changes:
            log_change(conn, "raw_ledger", str(rid), f, o, n, note, **ctx)
        # 向下游归一化表同步（发票台账编辑 -> 发票收款情况/经办人发票收款情况）
        if (old.get("kind") or "invoice") != "prepayment":
            _sync_invoice_from_raw(conn, rid, old, changes, data)
        if owns:
            conn.commit()
    finally:
        if owns:
            conn.close()
    return [f for f, _, _ in changes]


# ---------------------------------------------------------------------------
# 下游归一化表同步（发票台账编辑 -> invoice / charge_detail）
# ---------------------------------------------------------------------------
def _norm_date(conn, raw: str, rid: int) -> str:
    """台账原始日期文本 -> YYYY-MM-DD（无年时用批次账期/已有发票年份兜底）。"""
    if not raw or not raw.strip():
        return ""
    from app.importer.date_utils import normalize_date
    year = None
    b = conn.execute(
        "SELECT b.period FROM raw_ledger r LEFT JOIN import_batch b ON b.id=r.import_batch_id WHERE r.id=?",
        (rid,),
    ).fetchone()
    if b and b["period"]:
        try:
            year = int(str(b["period"]).split("-")[0])
        except (ValueError, TypeError):
            year = None
    if year is None:
        inv = conn.execute(
            "SELECT invoice_date FROM invoice WHERE invoice_no=(SELECT invoice_no FROM raw_ledger WHERE id=?)",
            (rid,),
        ).fetchone()
        if inv and inv["invoice_date"]:
            try:
                year = int(str(inv["invoice_date"]).split("-")[0])
            except (ValueError, TypeError):
                year = None
    try:
        return normalize_date(raw, default_year=year) or ""
    except Exception:
        return ""


def _sync_charge_detail(conn, invoice_no: str, handler_text: str, old_row: Dict) -> None:
    """按新经办人文本重算 charge_detail（保留 received_override 与手写行）。

    解析失败（经办人合计≠总额 / 含未上花名册姓名）时不改动现有分摊，避免误删/误建。
    """
    from app.importer.parse_handler import parse_handler_column
    from app.engine.backfill import staff_type_of

    total = conn.execute("SELECT total_amount FROM invoice WHERE invoice_no=?", (invoice_no,)).fetchone()
    total = total["total_amount"] if total else 0.0
    try:
        handlers = parse_handler_column(handler_text, total, invoice_no)
    except Exception:
        return  # 解析失败 -> 保持原分摊，交由上层交互式修正
    parsed_names = {n for n, _ in handlers}
    for name, amount in handlers:
        r = conn.execute(
            "SELECT id, received_override FROM charge_detail WHERE invoice_no=? AND person_name=?",
            (invoice_no, name),
        ).fetchone()
        ptype = staff_type_of(conn, name)
        if r:
            conn.execute(
                "UPDATE charge_detail SET billing_amount=?, person_type=? WHERE id=?",
                (amount, ptype, r["id"]),
            )
        else:
            batch = conn.execute(
                "SELECT import_batch_id FROM raw_ledger WHERE invoice_no=? ORDER BY id LIMIT 1",
                (invoice_no,),
            ).fetchone()
            bid = batch["import_batch_id"] if batch else None
            conn.execute(
                "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source, "
                "import_batch_id, person_type, src_sheet, src_row, received_override) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (invoice_no, name, amount, "import", bid, ptype,
                 old_row.get("sheet_name") or "", old_row.get("row_no") or 0, None),
            )
    # 删除该发票「import 来源且不再出现于新经办人列表」的行
    for r in conn.execute(
        "SELECT id, person_name FROM charge_detail WHERE invoice_no=? AND source='import'",
        (invoice_no,),
    ):
        if r["person_name"] not in parsed_names:
            conn.execute("DELETE FROM charge_detail WHERE id=?", (r["id"],))


def _sync_invoice_from_raw(conn, rid: int, old_row: Dict, changes, data: Dict) -> None:
    """把 raw_ledger 发票行的编辑结果同步到归一化 invoice / charge_detail 表。

    仅同步真正变更的字段：buyer / 日期 / 金额 / 案号 / 备注 / 经办人 / 发票号。
    """
    changed = {f: n for f, o, n in changes}
    old_no = (old_row.get("invoice_no") or "").strip()
    new_no = (data.get("invoice_no") or "").strip()

    # ---- 发票号码改名：谨慎级联引用表（延迟外键检查到事务提交，规避父/子更新顺序）----
    effective_no = old_no
    if rid and "invoice_no" in changed and new_no and new_no != old_no:
        clash = conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (new_no,)).fetchone()
        if not clash:
            conn.execute("PRAGMA defer_foreign_keys = ON")
            conn.execute("UPDATE invoice SET invoice_no=? WHERE invoice_no=?", (new_no, old_no))
            for tbl, col in (("charge_detail", "invoice_no"),
                             ("collection", "invoice_no"),
                             ("refund", "red_invoice_no"),
                             ("refund", "orig_invoice_no"),
                             ("prepayment_offset", "invoice_no")):
                conn.execute(f"UPDATE {tbl} SET {col}=? WHERE {col}=?", (new_no, old_no))
            effective_no = new_no
        else:
            # 新号已存在 -> 仅镜表已改，归一化表保持旧号，避免串数据
            log_change(conn, "raw_ledger", str(rid), "invoice_no_rename",
                       old_no, new_no, "新发票号已存在，未级联更名（仅镜表已改）",
                       friendly_table=build_friendly_table("raw_ledger", old_row.get("sheet_name") or ""),
                       invoice_no=old_no, buyer=(old_row.get("buyer") or "").strip(),
                       amount=(old_row.get("amount_raw") or "").strip(),
                       handlers=(old_row.get("handler_text") or "").strip())
    no = effective_no
    if not no:
        return

    # ---- invoice 主表：买/日期/金额/案号/备注 ----
    inv_sets, inv_params = [], []
    if "buyer" in changed:
        inv_sets.append("buyer=?"); inv_params.append(data.get("buyer") or "")
    if "invoice_date_raw" in changed:
        d = _norm_date(conn, data.get("invoice_date_raw") or "", rid)
        if d:
            inv_sets.append("invoice_date=?"); inv_params.append(d)
    if "amount_raw" in changed:
        inv_sets.append("total_amount=?"); inv_params.append(_parse_total(data.get("amount_raw") or ""))
    if "case_no" in changed:
        inv_sets.append("case_no=?"); inv_params.append(data.get("case_no") or "")
    if "remark" in changed:
        inv_sets.append("remark=?"); inv_params.append(data.get("remark") or "")

    if inv_sets:
        exists = conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (no,)).fetchone()
        if exists:
            conn.execute(f"UPDATE invoice SET {', '.join(inv_sets)} WHERE invoice_no=?",
                         inv_params + [no])
        else:
            # 极端情况：进项文档未建此发票 -> 补建最小发票行
            d = _norm_date(conn, data.get("invoice_date_raw") or "", rid) or \
                (data.get("invoice_date_raw") or "") or "1970-01-01"
            conn.execute(
                "INSERT OR IGNORE INTO invoice "
                "(invoice_no, invoice_date, buyer, total_amount, case_no, remark, source) "
                "VALUES (?,?,?,?,?,?,?)",
                (no, d, data.get("buyer") or "", _parse_total(data.get("amount_raw") or ""),
                 data.get("case_no") or "", data.get("remark") or "", "manual"))

    # ---- charge_detail：按 handler_text 重算 ----
    if "handler_text" in changed:
        _sync_charge_detail(conn, no, data.get("handler_text") or "", old_row)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _parse_total(txt: Optional[str]) -> float:
    if not txt:
        return 0.0
    m = re.search(r"-?\d[\d,]*\.?\d*", str(txt))
    if not m:
        return 0.0
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return 0.0
