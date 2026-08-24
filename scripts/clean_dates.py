"""历史日期统一清洗：把库内非规范日期改写为标准格式（幂等，可重复运行）

规则（保留信息量）：
- 3 段（年月日）→ YYYY-MM-DD，如 2024-9-15 / 2025.1.2 / 2025年1月2日 → 2025-01-02
- 2 段（年月）  → YYYY-MM，如 2025-1 / 2025.09 → 2025-09（collection.receipt_date 等月粒度字段）
- 两位年自动补 20xx（25.1.2 → 2025-01-02）

安全措施：
1. 运行前自动 checkpoint（WAL 合并）+ 备份 db 到 data/backups/lawfirm_<时间戳>.db
2. 全程单事务：任何失败回滚，不影响原库
3. 无法解析的非空值不动，仅在报告中列出数量与样例，由人工决定
4. 幂等：已规范的日期不会再被改写，可放心重复运行

用法：python scripts/clean_dates.py
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import DB_PATH, checkpoint, get_conn  # noqa: E402

# (表, 列, 说明)
DATE_FIELDS = [
    ("invoice", "invoice_date", "发票开票日期"),
    ("collection", "receipt_date", "收款日期/月"),
    ("refund", "refund_date", "退款日期"),
    ("prepayment", "received_date", "预收款收到日期"),
    ("prepayment_offset", "offset_date", "核销日期"),
    ("expense_ledger", "exp_date", "费用日期"),
    ("staff", "hire_month", "入职月份"),
]

_NUMS = re.compile(r"\d+")

# 各表主键（invoice 主键是 invoice_no，其余默认 id）
_PK = {"invoice": "invoice_no"}


def _valid(y: int, mo: int, d: int) -> bool:
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return False
    try:
        datetime(y, mo, d)
        return True
    except ValueError:
        return False


def norm_preserve(s: str):
    """规范化为标准日期，保留段数信息；空/无法解析返回 None"""
    s = (s or "").strip()
    if not s:
        return None

    # Excel 日期序列号（如 45658 → YYYY-MM-DD）
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        n = float(s)
        if 30000 <= n <= 60000:
            try:
                from openpyxl.utils.datetime import from_excel
                return from_excel(n).strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                return None

    nums = [int(x) for x in _NUMS.findall(s)]
    if len(nums) == 3:  # 年月日
        y, mo, d = nums
        if y < 100:
            y += 2000
        if not _valid(y, mo, d):
            return None
        return f"{y:04d}-{mo:02d}-{d:02d}"
    if len(nums) == 2:  # 年月
        y, mo = nums
        if y < 100:
            y += 2000
        if not (1 <= mo <= 12):
            return None
        return f"{y:04d}-{mo:02d}"
    return None


def backup_db() -> Path:
    checkpoint()  # WAL 合并，保证备份是完整单文件
    bak_dir = DB_PATH.parent / "backups"
    bak_dir.mkdir(parents=True, exist_ok=True)
    bak = bak_dir / f"lawfirm_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(DB_PATH, bak)
    return bak


def clean(conn: sqlite3.Connection):
    """返回汇总报告（dict: 表.列 -> (更新行数, 无法解析行数, 样例)）"""
    report = {}
    for table, col, label in DATE_FIELDS:
        pk = _PK.get(table, "id")
        try:
            rows = conn.execute(
                f"SELECT {pk} AS pk, {col} AS v FROM {table} WHERE {col} IS NOT NULL AND {col} != ''"
            ).fetchall()
        except sqlite3.OperationalError as e:
            print(f"  [跳过] {table}.{col}: {e}")
            continue

        updated = 0
        unparsable = []
        for r in rows:
            v = r["v"]
            nv = norm_preserve(v)
            if nv is None:
                unparsable.append(v)
                continue
            if nv != v:
                conn.execute(
                    f"UPDATE {table} SET {col}=? WHERE {pk}=?", (nv, r["pk"])
                )
                updated += 1
        report[f"{table}.{col}"] = (updated, len(unparsable), unparsable[:5], label)
    return report


def main() -> None:
    bak = backup_db()
    print(f"已备份到: {bak}")

    conn = get_conn()
    try:
        report = clean(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print("\n=== 清洗结果 ===")
    total_updated = 0
    total_unparsable = 0
    for key, (upd, unpars, samples, label) in report.items():
        total_updated += upd
        total_unparsable += unpars
        mark = "✅" if upd else "—"
        print(f"{mark} {key}（{label}）: 更新 {upd} 行", end="")
        if unpars:
            print(f"，无法解析 {unpars} 行，样例 {samples[:3]}", end="")
        print()
    print(f"\n共更新 {total_updated} 行，无法解析 {total_unparsable} 行（未改动，见上）")


if __name__ == "__main__":
    main()
