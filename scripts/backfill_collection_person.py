"""历史补录脚本：把发票级（person_name 为空）的收款明细，按经办人开票份额

比例拆分为逐经办人收款，并写入 person_name 列。

用途（方案 C 配套）：
- 新增 person_name 列后，历史台账导入的收款仍挂在整张发票上，需回填到经办人，
  以便结算能直接归因（无需再按比例分摊兜底）。
- 幂等：只处理 person_name 为空('')或 NULL 的 collection 行；已归因的行不动。

用法：
    python scripts/backfill_collection_person.py            # 正式执行
    python scripts/backfill_collection_person.py --dry-run  # 仅预览，不写库

注意：请在项目 venv 中运行（依赖 app 包），运行前建议先做一次快照。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本能 import app 包
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import get_conn  # noqa: E402


def split_amount(amount: float, billings: list) -> list:
    """按开票份额比例拆分 amount 到各经办人，末位吸收余数（保留 2 位）"""
    total = sum(b for _, b in billings)
    if total <= 0:
        return [(name, 0.0) for name, _ in billings]
    out = []
    allocated = 0.0
    for i, (name, b) in enumerate(billings):
        if i == len(billings) - 1:
            s = round(amount - allocated, 2)
        else:
            s = round(amount * b / total, 2)
            allocated += s
        out.append((name, s))
    return out


def backfill(dry_run: bool = False) -> dict:
    conn = get_conn()
    stats = {"invoices": 0, "rows_in": 0, "rows_out": 0, "skipped_no_handler": 0}
    try:
        # 找所有需要回填的发票（存在 person_name 为空的 collection 行）
        invs = conn.execute(
            "SELECT DISTINCT invoice_no FROM collection WHERE person_name IS NULL OR person_name = ''"
        ).fetchall()
        for r in invs:
            no = r["invoice_no"]
            handlers = conn.execute(
                "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=?", (no,)
            ).fetchall()
            if not handlers:
                stats["skipped_no_handler"] += 1
                continue
            billings = [(h["person_name"], h["billing_amount"]) for h in handlers]
            rows = conn.execute(
                "SELECT id, amount, receipt_date FROM collection "
                "WHERE invoice_no=? AND (person_name IS NULL OR person_name = '') ORDER BY id",
                (no,),
            ).fetchall()
            if not rows:
                continue
            stats["invoices"] += 1
            stats["rows_in"] += len(rows)
            new_rows = []
            for row in rows:
                shares = split_amount(row["amount"], billings)
                for name, amt in shares:
                    new_rows.append((no, amt, row["receipt_date"], name, row["id"]))
            stats["rows_out"] += len(new_rows)
            if not dry_run:
                for no_, amt, date_, name, old_id in new_rows:
                    conn.execute(
                        "INSERT INTO collection (invoice_no, amount, receipt_date, person_name, "
                        "source, import_batch_id) "
                        "SELECT invoice_no, ?, ?, ?, source, import_batch_id "
                        "FROM collection WHERE id=?",
                        (amt, date_, name, old_id),
                    )
                # 删除原发票级行
                conn.execute(
                    "DELETE FROM collection WHERE invoice_no=? AND (person_name IS NULL OR person_name = '')",
                    (no,),
                )
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="collection 收款明细回填 person_name")
    ap.add_argument("--dry-run", action="store_true", help="仅预览不写库")
    args = ap.parse_args()
    stats = backfill(dry_run=args.dry_run)
    mode = "预览(dry-run)" if args.dry_run else "已执行"
    print(f"[{mode}] 处理发票数={stats['invoices']}  "
          f"原收款行={stats['rows_in']} → 拆分后行={stats['rows_out']}  "
          f"跳过(无经办人)={stats['skipped_no_handler']}")


if __name__ == "__main__":
    main()
