"""补录（期初/历史应收）发票引擎

待补录 = 两个来源合并（按票号去重，源 A 优先）：
- 源 A「红字引用」：被 invoice.orig_invoice_no / refund.orig_invoice_no 引用、
  但 invoice 表中不存在的原票号（1.1 红字原票缺失 + 1.2 应收对应缺失）。
- 源 B「应收账款」：发票台账 sheet3（应收账款）中「开票月份 < 导入账期月份」，
  且 invoice 表中没有该票号的行（方案 A：导入时不再自动为它们建票，只写镜表）。
补录写入 invoice(source='manual') + charge_detail(经办人/开票金额) + collection(已收/日期)，
与现有手工补录完全一致，不影响其他页面（收款表/结算/退款判定）。
保存前校验经办人（至少一名 + 须在花名册），与导入写前校验共用 backfill.missing_handlers。

红字票的「金额 / 经办人」取法统一走 `load_red_reference()`（阶段 6 起）：预填补录弹窗
（`prefill_red_original`）与「红字 ⇄ 蓝字一致性比对」共用，避免两处 SQL 口径漂移。

写入拆两层（阶段 3 B2a，参照 `raw_ledger.update_row(conn=None)` 既有模式）：
- `build_backfill(conn, data)`  纯计算 + 校验 → 规范化 payload（不写库）；
- `apply_backfill(conn, payload)` 用传入 conn 写入，**不 commit**（事务归调用方）。
`save_backfill()` = 这两者 + 自开连接 commit，仅「发票补录」页（独立、立即写库）使用。
导入复核页的补录改为收集进 `data["backfills"]`，随发票台账**同一事务**入库
（见 `app.importer.importer.commit_ledger_import`）。
"""
from __future__ import annotations

from typing import Dict, List

from app.db import get_conn
from app.engine.backfill import HANDLER_WHITELIST, missing_handlers, norm_type, staff_type_of
from app.engine.collection import over_collection_message
from app.engine.raw_ledger import deferred_sheet3_invoices


def load_red_reference(conn, orig_no: str) -> Dict | None:
    """找引用原票 `orig_no` 的**红字发票** → 其信息；找不到返回 None。

    **红字票的「金额 / 经办人」取法唯一口径**（阶段 6）：`prefill_red_original`
    与复核页/补录页的「红字 vs 蓝字一致性比对」共用本函数，避免两处各写一份
    SQL 而口径漂移。

    返回 `{invoice_no, invoice_date, buyer, total_amount, handlers:[{name, billing}]}`
    —— **金额一律取绝对值**（红字在库是负数、蓝字是正数，符号差异不算不一致）。
    同一原票被多张红字票引用属异常，取开票日期最早的一张为参照。
    """
    red = conn.execute(
        "SELECT * FROM invoice WHERE orig_invoice_no=? AND total_amount < 0 "
        "ORDER BY invoice_date LIMIT 1",
        (orig_no,),
    ).fetchone()
    if red is None:
        return None
    cds = conn.execute(
        "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=? ORDER BY id",
        (red["invoice_no"],),
    ).fetchall()
    return {
        "invoice_no": red["invoice_no"],
        "invoice_date": red["invoice_date"] or "",
        "buyer": red["buyer"] or "",
        "total_amount": abs(red["total_amount"] or 0.0),
        "handlers": [
            {"name": cd["person_name"], "billing": abs(cd["billing_amount"] or 0.0)}
            for cd in cds
        ],
    }


def prefill_red_original(conn, orig_no: str) -> Dict:
    """源 A 预填：用引用该原票的红字发票（来自台账）信息预填。

    原票尚未入库（正因缺失才补录），拿不到原票开票日期 → 留空让用户手填，
    绝不能用红字发票的开票日期顶替。返回结构与源 B 预填一致。
    """
    ref = load_red_reference(conn, orig_no)
    if ref is None:
        return {"invoice_no": orig_no, "invoice_date": "", "buyer": "",
                "total_amount": None, "handlers": []}
    return {
        "invoice_no": orig_no,
        "invoice_date": "",      # 原票开票日期不可知，留空手填
        "buyer": ref["buyer"],
        "total_amount": ref["total_amount"],
        "handlers": [
            {"name": h["name"], "billing": h["billing"], "received": 0.0, "date": ""}
            for h in ref["handlers"]
        ],
    }


def list_pending_backfill(conn=None) -> List[Dict]:
    """待补录（双源合并，按票号去重，源 A「红字引用」优先）。

    每行字段：invoice_no / source / invoice_date / buyer / total_amount /
    handlers(源 B 预填，源 A 点击时由 prefill_red_original 现算) /
    status / collected / red_invoices
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        rows: List[Dict] = []
        seen = set()
        # ---- 源 A：红字 / 退款引用但 invoice 缺号 ----
        for r in conn.execute(
            "SELECT DISTINCT orig_invoice_no FROM ("
            "  SELECT orig_invoice_no FROM invoice WHERE orig_invoice_no IS NOT NULL AND orig_invoice_no <> ''"
            "  UNION"
            "  SELECT orig_invoice_no FROM refund WHERE orig_invoice_no IS NOT NULL AND orig_invoice_no <> ''"
            ")"
        ):
            orig = r["orig_invoice_no"]
            if orig in seen:
                continue
            seen.add(orig)
            if conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (orig,)).fetchone():
                continue  # 已存在，无需补录
            reds = [
                x["invoice_no"] for x in conn.execute(
                    "SELECT invoice_no FROM invoice WHERE orig_invoice_no=? AND total_amount < 0", (orig,)
                )
            ]
            is_red = bool(reds) or conn.execute(
                "SELECT 1 FROM refund WHERE orig_invoice_no=?", (orig,)
            ).fetchone() is not None
            pre = prefill_red_original(conn, orig)
            rows.append({
                "invoice_no": orig,
                "source": "红字引用",
                "invoice_date": pre.get("invoice_date") or "",
                "buyer": pre.get("buyer") or "",
                "total_amount": pre.get("total_amount"),
                "handlers": pre.get("handlers") or [],
                "status": "已被红冲" if is_red else "正常",
                "collected": 0.0,
                "red_invoices": reds,
            })
        # ---- 源 B：应收账款期外缺票（方案 A：导入时只写镜表）----
        for d in deferred_sheet3_invoices(conn=conn):
            no = d["invoice_no"]
            if no in seen:
                continue  # 已被源 A 覆盖（红字引用是更强的信号）
            seen.add(no)
            rows.append({
                "invoice_no": no,
                "source": "应收账款",
                "invoice_date": d["invoice_date"],
                "buyer": d["buyer"],
                "total_amount": d["total_amount"],
                "handlers": d["handlers"],
                "status": "正常",
                "collected": d["collected"],
                "red_invoices": [],
            })
        return rows
    finally:
        if own:
            conn.close()


def list_backfilled(conn=None) -> List[Dict]:
    """已补录：source='manual' 的发票。"""
    own = conn is None
    if own:
        conn = get_conn()
    try:
        rows: List[Dict] = []
        for inv in conn.execute(
            "SELECT invoice_no, invoice_date, buyer, total_amount, created_at "
            "FROM invoice WHERE source='manual' ORDER BY invoice_date DESC, invoice_no"
        ):
            collected = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM collection WHERE invoice_no=?", (inv["invoice_no"],)
            ).fetchone()[0]
            is_red = conn.execute(
                "SELECT 1 FROM invoice WHERE orig_invoice_no=? AND total_amount < 0", (inv["invoice_no"],)
            ).fetchone() is not None
            rows.append({
                "invoice_no": inv["invoice_no"],
                "invoice_date": inv["invoice_date"] or "",
                "buyer": inv["buyer"] or "",
                "total_amount": inv["total_amount"],
                "status": "已被红冲" if is_red else "正常",
                "collected": round(collected, 2),
                "created_at": inv["created_at"] or "",
            })
        return rows
    finally:
        if own:
            conn.close()


def load_invoice_detail(invoice_no: str) -> Dict:
    """读取一张发票的补录明细（编辑用）。"""
    conn = get_conn()
    try:
        inv = conn.execute("SELECT * FROM invoice WHERE invoice_no=?", (invoice_no,)).fetchone()
        if inv is None:
            return {}
        cds = conn.execute(
            "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=? ORDER BY id", (invoice_no,)
        ).fetchall()
        cols = conn.execute(
            "SELECT amount, receipt_date, person_name FROM collection "
            "WHERE invoice_no=? AND source='manual' ORDER BY id", (invoice_no,)
        ).fetchall()
        billing_map = {cd["person_name"]: cd["billing_amount"] for cd in cds}
        handlers: List[Dict] = []
        if cols:
            for c in cols:
                handlers.append({
                    "name": c["person_name"],
                    "billing": billing_map.get(c["person_name"], 0.0),
                    "received": c["amount"],
                    "date": c["receipt_date"] or "",
                })
        else:
            for cd in cds:
                handlers.append({
                    "name": cd["person_name"],
                    "billing": cd["billing_amount"],
                    "received": 0.0,
                    "date": "",
                })
        return {
            "invoice_no": inv["invoice_no"],
            "invoice_date": inv["invoice_date"] or "",
            "buyer": inv["buyer"] or "",
            "total_amount": inv["total_amount"],
            "handlers": handlers,
        }
    finally:
        conn.close()


def build_backfill(conn, data: Dict) -> Dict:
    """补录表单 → 规范化 payload（**纯计算 + 校验，绝不写库**）。

    返回：
      {invoice_no, invoice_date, buyer, total_amount,
       charge: [(name, billing, person_type), ...],   # 按姓名聚合后的开票分摊
       collections: [(name, amount, receipt_date), ...]}  # 已收>0 且有日期

    校验（不通过抛 ValueError，由 UI 就地提示；与导入写前校验同一口径）：
    - 发票号码 / 开票日期必填；
    - 至少一名经办人（否则该票不进分账）；
    - 经办人须在花名册（或白名单）—— 与 `missing_handlers` 完全同源；
    - 已收金额合计不得超过价税合计（超过 = 台账信息错误，不静默写入）。

    拆出本函数的目的（阶段 3 B2a）：让「补录随台账同一事务入库」可以把
    **校验**与**写入**分开跑 —— 校验在弹窗确定时与入库前各跑一次（B2d），
    写入由 `apply_backfill` 用导入自己的连接完成。
    """
    no = (data.get("invoice_no") or "").strip()
    if not no:
        raise ValueError("发票号码不能为空")
    date = (data.get("invoice_date") or "").strip()
    if not date:
        raise ValueError("开票日期不能为空")
    total = float(data.get("total_amount") or 0)
    handlers = list(data.get("handlers") or [])
    # ① 至少一名经办人：否则该票不写 charge_detail → 不计入任何人的开票额 → 分账少整张票。
    # ② 经办人须在花名册（或白名单）：历史票经办人可能已离职，故花名册口径不过滤
    #    is_active；但名字写错/未登记必须拦下，否则会静默落成 person_type='其他'
    #    → 该人在结算里业务收入按 0 计、按身份筛选也看不到。
    names = [(h.get("name") or "").strip() for h in handlers]
    names = [n for n in names if n]
    if not names:
        raise ValueError(
            "至少要填写一名经办人（用于分账）。\n"
            "如该发票确实需要新增经办人，请先在「员工管理」中登记后再补录。"
        )
    miss = missing_handlers(conn, names)
    if miss:
        raise ValueError(
            "经办人不在职工花名册中（请先在员工管理中添加）: " + "、".join(miss)
        )
    # 已收合计 > 价税合计 → 拒绝（与弹窗内的即时提示同一口径，写库前再兜一次）
    sum_rec = sum(float(h.get("received") or 0) for h in handlers)
    if total > 0 and sum_rec > total + 0.01:
        raise ValueError(f"已收金额合计({sum_rec:,.2f})超过价税合计({total:,.2f})")
    # 按经办人聚合开票金额（双人名容错）
    agg: Dict[str, float] = {}
    for h in handlers:
        nm = (h.get("name") or "").strip()
        if not nm:
            continue
        agg[nm] = agg.get(nm, 0.0) + float(h.get("billing") or 0)
    charge = [
        (nm, billing, norm_type(staff_type_of(conn, nm)))
        for nm, billing in agg.items()
    ]
    # collection：明细表每行（已收金额>0 且有收款日期）一笔
    collections = []
    for h in handlers:
        nm = (h.get("name") or "").strip()
        amt = float(h.get("received") or 0)
        rdate = (h.get("date") or "").strip()
        if nm and amt > 0.001 and rdate:
            collections.append((nm, amt, rdate[:10]))
    return {
        "invoice_no": no,
        "invoice_date": date,
        "buyer": (data.get("buyer") or "").strip(),
        "total_amount": total,
        "charge": charge,
        "collections": collections,
    }


def apply_backfill(conn, payload: Dict) -> str | None:
    """用**传入的 conn** 写入一张补录（**不 commit、不 close**，事务归调用方）。

    payload = `build_backfill()` 的返回值。写入顺序与旧 `save_backfill` 一致：
    清该票 manual 旧数据 → upsert invoice(source='manual') → charge_detail → collection。

    两条硬边界：
    1. **只清 `source='manual'`** —— 该票的 import 收款（台账写的）绝不动
       （D1 甲：`_write_collection_for_invoice` 的整票 DELETE 是地雷，本项目不用它）。
    2. **不覆盖非 manual 票**：若该票已在库且来源不是 manual（如销项导入），抛
       ValueError —— 绝不把销项票静默改成 manual（B2e 的可诊断报错由此产生）。
    3. **不写 `received_snapshot`**、`invoice.import_batch_id` **留空**（B2f）：
       补录票不属任何导入批次，撤销该批台账不应连带删掉补录。

    超收校验（用户铁律：累计收款不得超开票净额）：**只读预校验**放在所有写操作之前，
    确保"早返"不污染既有数据（不删 manual、不插新行）。
    - 新票（库内无该票）：直接比 本次补录收款 ≤ 开票净额；
    - 已在库票：调 `over_collection_message`（exclude_sources=("manual",)，旧 manual
      即将被删，故只算 import 既有 + 本次 manual）。
    返回 None = 已写入；返回文案 = 超收被拦（由导入路径记入 problems / 独立页抛 ValueError）。
    """
    no = payload["invoice_no"]
    exist = conn.execute("SELECT source, total_amount FROM invoice WHERE invoice_no=?", (no,)).fetchone()
    # ---- 只读超收预校验（不得先于任何写操作）----
    incoming = round(sum(float(a) for _n, a, _d in payload.get("collections") or []), 2)
    if incoming > 0.001:
        if exist is None:
            face = float(payload.get("total_amount") or 0.0)
            if incoming > face + 0.01:
                return (f"该发票金额 {face:,.2f} 元，本次补录收款 {incoming:,.2f} 元，"
                        f"已超出开票净额，请核对待补录金额。")
        else:
            msg = over_collection_message(conn, no, incoming, exclude_sources=("manual",))
            if msg:
                return msg
    # 清旧 manual 数据（允许重复保存 / 编辑；import 数据一律不动）
    conn.execute("DELETE FROM charge_detail WHERE invoice_no=? AND source='manual'", (no,))
    conn.execute("DELETE FROM collection WHERE invoice_no=? AND source='manual'", (no,))
    exist = conn.execute("SELECT source FROM invoice WHERE invoice_no=?", (no,)).fetchone()
    if exist:
        if (exist["source"] or "") != "manual":
            raise ValueError(
                f"发票 {no} 已在库中（来源：{exist['source'] or '未知'}），不能以补录方式覆盖。\n"
                "该票无需补录；请刷新复核页后重试，或核对是否重复登记了同一票号。"
            )
        conn.execute(
            "UPDATE invoice SET invoice_date=?, buyer=?, total_amount=?, source='manual' "
            "WHERE invoice_no=?",
            (payload["invoice_date"], payload["buyer"], payload["total_amount"], no),
        )
    else:
        conn.execute(
            "INSERT INTO invoice "
            "(invoice_no, invoice_date, buyer, total_amount, kind, source, created_at) "
            "VALUES (?,?,?,?,?,?, datetime('now','localtime'))",
            (no, payload["invoice_date"], payload["buyer"], payload["total_amount"], "", "manual"),
        )
    for nm, billing, ptype in payload["charge"]:
        conn.execute(
            "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source, person_type) "
            "VALUES (?,?,?,?,?)",
            (no, nm, billing, "manual", ptype),
        )
    for nm, amt, rdate in payload["collections"]:
        conn.execute(
            "INSERT INTO collection (invoice_no, amount, receipt_date, person_name, source, note) "
            "VALUES (?,?,?,?,?,?)",
            (no, amt, rdate, nm, "manual", "补录"),
        )
    return None


def save_backfill(data: Dict, editing: bool = False) -> None:
    """保存补录（新增/编辑通用）—— **独立页面的立即写库路径**。

    data: {invoice_no, invoice_date, buyer, total_amount,
           handlers:[{name, billing, received, date}]}

    「发票补录」页专用：自己开连接、自己 commit（B2i/B2k：该页不属任何批次，
    保持立即写库不变）。导入复核页的补录**不走这里** —— 它收集进 data["backfills"]，
    由 `app.importer.importer.commit_ledger_import` 在同一事务里调 `apply_backfill`。

    editing 仅为兼容旧调用保留（初版即未使用）。
    """
    conn = get_conn()
    try:
        payload = build_backfill(conn, data)
        msg = apply_backfill(conn, payload)
        if msg:
            conn.rollback()
            raise ValueError(msg)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_backfill(invoice_no: str) -> None:
    """删除一张补录发票（仅 manual 来源）。被退款引用时阻止。"""
    conn = get_conn()
    try:
        if conn.execute("SELECT 1 FROM refund WHERE orig_invoice_no=?", (invoice_no,)).fetchone():
            raise ValueError(
                f"发票 {invoice_no} 存在已确认的退款记录，无法删除。请先删除对应退款记录。"
            )
        conn.execute("DELETE FROM charge_detail WHERE invoice_no=? AND source='manual'", (invoice_no,))
        conn.execute("DELETE FROM collection WHERE invoice_no=? AND source='manual'", (invoice_no,))
        conn.execute("DELETE FROM invoice WHERE invoice_no=? AND source='manual'", (invoice_no,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
