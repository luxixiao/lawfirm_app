"""费用台账详情页保存：把编辑后的行一次性写入 expense_ledger + change_log。

纯逻辑、无 Qt 依赖，可无头单测。被 app/ui/expense_detail_view._save 调用；
事务由调用方开启 / 提交 / 回滚（本模块只负责在事务内执行 UPDATE 与写 change_log）。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from app.engine.change_log import log_change

# 详情页可编辑字段（方案 §2.3）：含 exp_date/ticket_no/person_type；
# 不含 period/seq/source/id/import_batch_id/book_amount（book_amount 自动算且只读）
EXPENSE_EDITABLE_KEYS = {
    "name", "exp_date", "ticket_no", "handler", "actual_handler",
    "expense_amount", "tax_amount", "expense_type", "voucher_no",
    "subject1", "subject2", "person_type",
}


def apply_expense_edits(conn, edited: List[Tuple[int, Dict, Dict]]) -> int:
    """在同一事务内把 edited 的改动写库 + 写 change_log（调用方负责 commit/rollback）。

    edited: [(rid, orig, new), ...]
      - rid:  行 id（expense_ledger.id）
      - orig: 编辑前整行 dict（用于算字段级 diff 与 change_log 旧值）
      - new:  编辑后 dict，含全部 EXPENSE_EDITABLE_KEYS + 自动算的 book_amount
    返回实际执行 UPDATE 的行数（无变动的行跳过，不写 change_log）。
    """
    n = 0
    for rid, orig, new in edited:
        data_changed = [
            (k, orig.get(k), new[k])
            for k in EXPENSE_EDITABLE_KEYS
            if str(orig.get(k) or "") != str(new.get(k) or "")
        ]
        book_changed = str(orig.get("book_amount")) != str(new["book_amount"])
        if not data_changed and not book_changed:
            continue

        cols = [k for k, _o, _n in data_changed]
        if book_changed:
            cols.append("book_amount")
        cols = list(dict.fromkeys(cols))  # 保序去重（book_amount 落最后）

        sets = ", ".join(f"{c}=?" for c in cols)
        params = [new[c] for c in cols]
        conn.execute(
            f"UPDATE expense_ledger SET {sets} WHERE id=?", (*params, rid))
        log_change(conn, "expense_ledger", str(rid), "edit", "", "", "费用台账编辑")
        for k, o, n_ in data_changed:
            log_change(conn, "expense_ledger", str(rid), k, o, n_, "费用台账编辑")
        if book_changed:
            log_change(conn, "expense_ledger", str(rid), "book_amount",
                      orig.get("book_amount"), new["book_amount"], "费用台账编辑")
        n += 1
    return n
