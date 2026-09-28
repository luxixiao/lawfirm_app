"""② 第3步：list_pending_backfill 源 A 加 raw_invoice.synced=0 排除的单测（内存库隔离）。

问题背景：销项导入后，蓝字原票先落 raw_invoice（synced=0，待 sync to invoice），
尚未同步进 invoice。此时若某红字/退款引用了该蓝字原票，源 A 会因「原票不在 invoice」
误报为待补录。第3步在源 A 加排除：原票位于 raw_invoice 且 synced=0 → 视为「待同步」排除。

场景：
- BLUE_PENDING：红字引用原票，原票不在 invoice，但在 raw_invoice（synced=0，待同步）→ 排除
- BLUE_MISSING：红字引用原票，原票不在 invoice，也不在 raw_invoice → 仍列入待补录
- BLUE_INLIB ：红字引用原票，原票已在 invoice → 排除（既有行为）
- BLUE_SYNED1 ：红字引用原票，原票不在 invoice，但在 raw_invoice（synced=1，已同步语义）→ 仍列入（不误伤 synced=0 之外的行）

运行：python tests/test_backfill_synced_exclusion.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import backfill_module as bm  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


class _Proxy:
    def __init__(self, c):
        self._c = c

    def execute(self, *a, **k):
        return self._c.execute(*a, **k)


def main() -> int:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    proxy = _Proxy(conn)
    # list_pending_backfill / prefill_red_original 都走我们传入的 conn
    bm.get_conn = lambda: proxy

    # 三张红字发票，各自引用一个缺失原票
    conn.execute(
        "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source, orig_invoice_no) "
        "VALUES ('RED_PEND', '2025-06-20', '乙公司', -500.0, 'import', 'BLUE_PENDING')")
    conn.execute(
        "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source, orig_invoice_no) "
        "VALUES ('RED_MISS', '2025-06-20', '丙公司', -200.0, 'import', 'BLUE_MISSING')")
    conn.execute(
        "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source, orig_invoice_no) "
        "VALUES ('RED_LIB', '2025-06-20', '丁公司', -300.0, 'import', 'BLUE_INLIB')")
    # BLUE_SYNED1 由一张红字引用（synced=1 但不在 invoice）→ 不应被排除逻辑误伤
    conn.execute(
        "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source, orig_invoice_no) "
        "VALUES ('RED_SYN1', '2025-06-20', '戊公司', -100.0, 'import', 'BLUE_SYNED1')")
    # BLUE_INLIB 已在 invoice（已在库）
    conn.execute(
        "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
        "VALUES ('BLUE_INLIB', '2025-05-10', '丁公司', 300.0, 'import')")
    # BLUE_PENDING 在 raw_invoice，待同步 synced=0（销项导入后尚未 sync to invoice）
    conn.execute("INSERT INTO raw_invoice (invoice_no, synced) VALUES ('BLUE_PENDING', 0)")
    # 对照组：raw_invoice 中 synced=1（已同步语义）但不在 invoice → 不应被排除逻辑误伤
    conn.execute("INSERT INTO raw_invoice (invoice_no, synced) VALUES ('BLUE_SYNED1', 1)")
    conn.commit()

    pend = {p["invoice_no"] for p in bm.list_pending_backfill(conn=proxy)}
    check("synced=0 待同步原票 → 排除（不误报待补录）", "BLUE_PENDING" not in pend, str(sorted(pend)))
    check("真正缺失原票 → 仍列入待补录", "BLUE_MISSING" in pend, str(sorted(pend)))
    check("已在库原票 → 排除（既有行为）", "BLUE_INLIB" not in pend, str(sorted(pend)))
    check("raw_invoice synced=1 但不在 invoice → 仍列入（不误伤）",
          "BLUE_SYNED1" in pend, str(sorted(pend)))

    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + " | ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
