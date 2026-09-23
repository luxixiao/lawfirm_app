"""费用台账编辑 / 同账期重导 共用的校验与比对内核（纯逻辑，可无头单测）。

- ``Violation`` / ``validate_expense_edit``：详情页保存与导入共用的行级校验
  （与导入口径一致：账面金额=费用−税额、科目在会计科目主表、经办人参与结算）。
- ``ReimportDiff`` / ``diff_expense_reimport``：同账期重导的字段级 diff（不落库）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.db import get_conn
from app.engine import account_subject as asub
from app.engine import staff_type as st


def _own_conn(conn=None):
    return conn is None, conn if conn is not None else get_conn()


def _close(own: bool, conn) -> None:
    if own:
        conn.close()


# 详情保存 / 重导 diff 仅比较的「可编辑字段」（忽略 id/import_batch_id/source 等系统字段）
EDIT_FIELDS = (
    "expense_amount", "tax_amount", "book_amount", "name", "expense_type",
    "subject1", "subject2", "handler", "actual_handler", "exp_date",
    "ticket_no", "voucher_no", "person_type",
)


@dataclass
class Violation:
    """单行校验失败项。"""
    row_id: int
    field: str
    message: str


@dataclass
class ReimportDiff:
    """同账期重导的字段级 diff（内存，不落库）。"""
    period: str
    parsed: List[dict]
    db: List[dict]
    matched: List[Tuple[int, List[dict]]] = field(default_factory=list)   # (seq, [field_diff...])
    added: List[int] = field(default_factory=list)                        # 仅新文件有（按 seq）
    removed: List[int] = field(default_factory=list)                     # 仅旧库有（按 seq）
    unpaired: List[str] = field(default_factory=list)                    # P1-4：无/坏序号行（显式报告，不静默）

    @property
    def changed(self) -> bool:
        """有任一差异（字段变更 / 新增 / 删除 / 无法配对）即为 changed。"""
        return bool(self.matched or self.added or self.removed or self.unpaired)


def validate_expense_edit(rows: List[dict], conn=None) -> List[Violation]:
    """详情页保存校验（与导入口径一致）。

    rows: 编辑后整行 dict（含 id / expense_amount / tax_amount / book_amount /
           subject1 / subject2 / actual_handler ...）。
    ① book_amount ≈ expense_amount - tax_amount (±0.01)；
    ② (subject1, subject2) 必须都在会计科目主表（复用 asub.validate_ledger_subjects）；
    ③ actual_handler 必须是参与结算人员（复用 st.is_settle_participant）。
    返回 [] = 通过。
    """
    own, c = _own_conn(conn)
    try:
        violations: List[Violation] = []

        # ① 账面金额 ≈ 费用金额 − 税额（±0.01）
        # ② 经办人必须参与结算
        for idx, row in enumerate(rows):
            rid = row.get("id", row.get("seq", idx))
            amt = float(row.get("expense_amount") or 0)
            tax = float(row.get("tax_amount") or 0)
            book = float(row.get("book_amount") or 0)
            if abs(book - (amt - tax)) > 0.01:
                violations.append(Violation(
                    row_id=rid, field="book_amount",
                    message=f"账面费用金额 {book} 与 费用金额−税额 {amt - tax} 不符（±0.01）"))
            handler = (row.get("actual_handler") or "").strip()
            if handler and not st.is_settle_participant(handler, c):
                violations.append(Violation(
                    row_id=rid, field="actual_handler",
                    message=f"经办人 {handler} 未参与结算（员工类型页未开启参与结算）"))

        # ② 科目必须在会计科目主表（复用校验内核，不重写科目树遍历）
        bad = set(asub.validate_ledger_subjects(rows, c))
        if bad:
            l1, _ = asub.subject_sets(c)
            for idx, row in enumerate(rows):
                s1 = (row.get("subject1") or "").strip()
                s2 = (row.get("subject2") or "").strip()
                if (s1, s2) in bad:
                    rid = row.get("id", row.get("seq", idx))
                    fld = "subject1" if (not s1 or s1 not in l1) else "subject2"
                    violations.append(Violation(
                        row_id=rid, field=fld,
                        message=f"科目 {s1}/{s2} 不在会计科目主表"))

        return violations
    finally:
        _close(own, c)


def diff_expense_reimport(parsed_rows: List[dict], db_rows: List[dict]) -> ReimportDiff:
    """按 seq 配对，字段级 diff（同账期重导用，不落库）。

    仅比较 EDIT_FIELDS（金额/科目/经办人/名称/类型…），忽略 id/import_batch_id/source。
    matched = 两表都有且字段有差异的 (seq, [field_diff...])；
    added   = 仅新文件有（按 seq）；removed = 仅旧库有（按 seq）。
    unpaired = 无有效序号的行描述（P1-4：旧代码 int(None)/int("3月") 直接崩溃，
               现按无法配对显式报告，随 diff 一并展示给用户，不静默）。
    """
    period = ""
    if parsed_rows:
        period = parsed_rows[0].get("period", "") or period
    elif db_rows:
        period = db_rows[0].get("period", "") or period

    def _key(r: dict):
        try:
            return int(r.get("seq"))
        except (TypeError, ValueError):
            return None

    parsed_by_seq: dict = {}
    unpaired: List[str] = []
    for r in parsed_rows:
        s = _key(r)
        if s is None:
            raw = r.get("seq_raw")
            unpaired.append(f"新文件「{r.get('name') or '?'}」序号无法解析："
                            f"「{raw if raw else '空'}」")
            continue
        parsed_by_seq[s] = r
    db_by_seq: dict = {}
    for r in db_rows:
        s = _key(r)
        if s is None:
            unpaired.append(f"库内 id={r.get('id')}「{r.get('name') or '?'}」无有效序号")
            continue
        db_by_seq[s] = r

    matched: List[Tuple[int, List[dict]]] = []
    added: List[int] = []
    removed: List[int] = []

    for seq, prow in parsed_by_seq.items():
        if seq not in db_by_seq:
            added.append(seq)
            continue
        drow = db_by_seq[seq]
        diffs = []
        for f in EDIT_FIELDS:
            pv = prow.get(f)
            dv = drow.get(f)
            # 数值字段浮点容差比较，其余按原值
            if isinstance(pv, float) or isinstance(dv, float):
                if float(pv or 0) != float(dv or 0):
                    diffs.append({"field": f, "parsed": pv, "db": dv})
            elif pv != dv:
                diffs.append({"field": f, "parsed": pv, "db": dv})
        if diffs:
            matched.append((seq, diffs))

    for seq in db_by_seq:
        if seq not in parsed_by_seq:
            removed.append(seq)

    return ReimportDiff(
        period=period, parsed=parsed_rows, db=db_rows,
        matched=matched, added=added, removed=removed, unpaired=unpaired)
