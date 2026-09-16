"""import_fix_log（导入前留痕引擎）单元测试 — 内存库。

运行：python tests/test_import_fix_log.py（venv python，依赖 xlrd）
覆盖：fix 单条 (导入修正) + edit 逐字段 + 收款明细摘要 + synced=0 +
friendly_table='发票台账 · 导入修正'；查不到镜表行 skip；空 items 安全。
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db as _db_mod  # noqa: E402
from app.engine.import_fix_log import log_import_fixes  # noqa: E402

OK, FAILS = 0, []


class _ConnProxy:
    def __init__(self, c):
        self._c = c
    def execute(self, *a, **k):
        return self._c.execute(*a, **k)
    def commit(self):
        self._c.commit()
    def rollback(self):
        self._c.rollback()
    def close(self):
        pass
    def __getattr__(self, n):
        return getattr(self._c, n)


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


def main() -> int:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    proxy = _ConnProxy(conn)
    _orig = _db_mod.get_conn
    _db_mod.get_conn = lambda: proxy
    _db_mod.init_db()
    _db_mod.get_conn = _orig

    import app.engine.import_fix_log as _ifl
    _ifl.get_conn = lambda: proxy

    def q(sql, *args):
        return conn.execute(sql, args).fetchall()

    def q1(sql, *args):
        return conn.execute(sql, args).fetchone()

    conn.execute(
        "INSERT INTO import_batch (id, batch_type, period, file_name, status, imported_at) "
        "VALUES (1,'ledger','2025-01','f.xlsx','active', datetime('now','localtime'))")
    for i, no in enumerate(("A1", "A2", "A3"), 1):
        conn.execute(
            "INSERT INTO raw_ledger (id, sheet_key, sheet_name, row_no, invoice_no, "
            "buyer, amount_raw, amount_num, handler_text, remark, kind, synced, "
            "import_batch_id) VALUES (?, 'sheet1','已开票已入账',2,?,'甲公司','100.0',"
            "100.0,'张三100','','invoice',1,1)", (i, no))
    conn.commit()

    # ---------------- 空 items ----------------
    r = log_import_fixes(1, [])
    check("空 items 安全", r == {"logged": 0, "skipped": 0})

    # ---------------- fix：修正问题行 ----------------
    # old = 该问题行在台账里的原始文本（解析失败当时的原文，经办人列留空）
    fix_item = {
        "kind": "fix", "invoice_no": "A1",
        "old": {"invoice_date": "25.1.10", "total_amount": "200",
                "handler_text": "", "buyer": "甲公司", "remark_raw": ""},
        "new": {"invoice_date": "2025-01-10", "total_amount": 200.0,
                "handler_text": "张三200", "buyer": "甲公司",
                "split_receipts": [("张三", 200.0, "2025-02")]},
    }
    # ---------------- edit：编辑已解析行 ----------------
    edit_item = {
        "kind": "edit", "invoice_no": "A2",
        "old": {"invoice_date": "2025-01-05", "total_amount": 100.0,
                "buyer": "甲公司", "case_no": "", "handler_text": "张三100",
                "split_receipts": []},
        "new": {"invoice_date": "2025-01-06", "total_amount": 120.0,
                "buyer": "甲公司", "case_no": "",
                "handler_text": "张三60、李四60",
                "split_receipts": [("张三", 120.0, "2025-02")]},
    }
    ghost = {"kind": "fix", "invoice_no": "GHOST", "old": {},
             "new": {"total_amount": 50.0}}
    r = log_import_fixes(1, [fix_item, edit_item, ghost])
    check("logged=2 / skipped=1", r == {"logged": 2, "skipped": 1}, str(r))

    # ---- fix 记录 ----
    rows = q("SELECT field, old_value, new_value, note, friendly_table, record_id "
             "FROM change_log WHERE record_id='1' ORDER BY id")
    check("fix 记 1 条 (导入修正)", len(rows) == 1 and rows[0]["field"] == "(导入修正)",
          str([dict(x) for x in rows]))
    check("fix old = 原始台账原文摘要",
          "开票日期 25.1.10" in rows[0]["old_value"]
          and "金额 200" in rows[0]["old_value"]
          and "经办人" not in rows[0]["old_value"],
          rows[0]["old_value"])
    check("fix new 含金额与经办人",
          "金额 200" in rows[0]["new_value"] and "张三200" in rows[0]["new_value"],
          rows[0]["new_value"])
    check("fix friendly_table = 发票台账 · 导入修正",
          rows[0]["friendly_table"] == "发票台账 · 导入修正", rows[0]["friendly_table"])
    check("fix note = 导入时人工修正", rows[0]["note"] == "导入时人工修正")

    # ---- edit 记录（逐字段 + 收款明细）----
    rows = q("SELECT field, old_value, new_value, note FROM change_log "
             "WHERE record_id='2' ORDER BY id")
    by = {r["field"]: r for r in rows}
    check("edit 记 4 条（日期/金额/经办人/收款明细）", len(rows) == 4,
          str([dict(x) for x in rows]))
    check("日期 old→new", by["invoice_date"]["old_value"] == "2025-01-05"
          and by["invoice_date"]["new_value"] == "2025-01-06")
    check("金额 old→new", by["total_amount"]["old_value"] == "100"
          and by["total_amount"]["new_value"] == "120")
    check("收款明细摘要含新值", "张三 120" in by["收款明细"]["new_value"],
          by["收款明细"]["new_value"])
    check("edit 未变字段（buyer）不记", "buyer" not in by)
    check("edit 经办人变更已记", by["handler_text"]["old_value"] == "张三100"
          and by["handler_text"]["new_value"] == "张三60、李四60")
    check("edit note = 导入时人工编辑", rows[0]["note"] == "导入时人工编辑")
    # 收款明细 old 侧标记
    check("收款明细 old 侧标记 (按原备注)", by["收款明细"]["old_value"] == "(按原备注)")

    # ---- synced=0 ----
    check("A1/A2 synced=0", q1("SELECT synced FROM raw_ledger WHERE id=1")["synced"] == 0
          and q1("SELECT synced FROM raw_ledger WHERE id=2")["synced"] == 0)
    check("A3（未干预）synced 保持 1",
          q1("SELECT synced FROM raw_ledger WHERE id=3")["synced"] == 1)
    check("GHOST 未留痕", q1("SELECT count(*) FROM change_log WHERE record_id='GHOST'")[0] == 0)

    conn.close()
    print(f"\n{OK} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
