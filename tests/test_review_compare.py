"""review_compare（导入后融合比对引擎）单元测试 — 内存库。

运行：python tests/test_review_compare.py
覆盖：镜表↔业务表 逐维度比对口径（金额/经办人分摊/已收认定）、
仅源有/仅库有、纯人名均分豁免、快照优先/账期窗口回退、anomaly_note
dim='merged' 优先 + 旧维度聚合回退、补录（source='manual'）纳入比对口径。
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import review_compare as rc  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


def row_of(rows, no):
    for r in rows:
        if r["invoice_no"] == no:
            return r
    return None


def main() -> int:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    class _ConnProxy:
        """引擎 finally 会 close()（应用里每次 get_conn 新建连接）；
        内存库单例连接由本测试持有，close 置空操作防误关。"""
        def __init__(self, c):
            self._c = c
        def execute(self, *a, **k):
            return self._c.execute(*a, **k)
        def commit(self):
            self._c.commit()
        def close(self):
            pass
        def __getattr__(self, n):
            return getattr(self._c, n)

    rc.get_conn = lambda: _ConnProxy(conn)  # 注入内存库

    P = "2025-01"
    conn.execute(
        "INSERT INTO import_batch (id, batch_type, period, file_name, status, imported_at) "
        "VALUES (1,'ledger',?,'2025.1台账.xlsx','active', datetime('now','localtime'))", (P,))

    def add_raw(no, amount, handler, buyer="甲公司", remark="", rid=0):
        conn.execute(
            "INSERT INTO raw_ledger (id, sheet_key, sheet_name, row_no, invoice_no, "
            "buyer, amount_num, handler_text, remark, kind, synced, import_batch_id) "
            "VALUES (?, 'sheet1', '已开票已入账', 2, ?, ?, ?, ?, ?, 'invoice', 1, 1)",
            (rid, no, buyer, amount, handler, remark))

    def add_inv(no, amount, buyer="甲公司"):
        conn.execute(
            "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source, import_batch_id) "
            "VALUES (?, '2025-01-05', ?, ?, 'import', 1)", (no, buyer, amount))

    def add_cd(no, pairs):
        for name, amt in pairs.items():
            conn.execute(
                "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source) "
                "VALUES (?, ?, ?, 'import')", (no, name, amt))

    def add_snap(no, exp_total, act_total, items_e=None, items_a=None):
        conn.execute(
            "INSERT INTO received_snapshot (import_batch_id, invoice_no, expected_json, actual_json) "
            "VALUES (1, ?, ?, ?)",
            (no, json.dumps({"total": exp_total, "items": items_e or []}),
             json.dumps({"total": act_total, "items": items_a or []})))

    # ---------- 无批次 ----------
    b, rows = rc.build_review_rows("2099-01")
    check("无批次 → (None, [])", b is None and rows == [])

    # ---------- 1) 全一致 ----------
    add_raw("A1", 100.0, "张三100", rid=11)
    add_inv("A1", 100.0)
    add_cd("A1", {"张三": 100.0})
    b, rows = rc.build_review_rows(P)
    r = row_of(rows, "A1")
    check("一致：状态", r["status"] == "一致", r["status"])
    check("一致：来源带 sheet 行号", "已开票已入账" in r["source"] and "第2行" in r["source"],
          r["source"])

    # ---------- 2) 金额不一致 ----------
    add_raw("A2", 100.0, "张三100", rid=12)
    add_inv("A2", 200.0)
    add_cd("A2", {"张三": 200.0})
    r = row_of(rc.build_review_rows(P)[1], "A2")
    check("金额不一致 → 不符", r["status"] == "不符" and "金额不一致" in r["detail"],
          f"{r['status']}/{r['detail']}")

    # ---------- 3) 经办人缺失/多出 ----------
    add_raw("A3", 100.0, "张三60、李四40", rid=13)
    add_inv("A3", 100.0)
    add_cd("A3", {"张三": 100.0})
    r = row_of(rc.build_review_rows(P)[1], "A3")
    check("库内缺经办人 → 不符", any("经办人缺失" in f for f in r["flags"]), r["detail"])

    add_raw("A4", 100.0, "张三100", rid=14)
    add_inv("A4", 100.0)
    add_cd("A4", {"张三": 60.0, "李四": 40.0})
    r = row_of(rc.build_review_rows(P)[1], "A4")
    check("库内多经办人 → 不符", any("经办人多出" in f for f in r["flags"]), r["detail"])

    # ---------- 4) 分摊金额不符 ----------
    add_raw("A5", 100.0, "张三60、李四40", rid=15)
    add_inv("A5", 100.0)
    add_cd("A5", {"张三": 70.0, "李四": 30.0})
    r = row_of(rc.build_review_rows(P)[1], "A5")
    check("分摊金额不符 → 不符", any("分摊金额不符" in f for f in r["flags"]), r["detail"])

    # ---------- 5) 纯人名均分豁免 ----------
    add_raw("A6", 100.0, "张三、李四", rid=16)
    add_inv("A6", 100.0)
    add_cd("A6", {"张三": 60.0, "李四": 40.0})
    r = row_of(rc.build_review_rows(P)[1], "A6")
    check("纯人名均分沿用首月拆分 → 一致", r["status"] == "一致",
          f"{r['status']}/{r['detail']}")

    # ---------- 6) 已收认定不符（快照优先） ----------
    add_raw("A7", 100.0, "张三100", rid=17)
    add_inv("A7", 100.0)
    add_cd("A7", {"张三": 100.0})
    add_snap("A7", 100.0, 50.0)
    r = row_of(rc.build_review_rows(P)[1], "A7")
    check("已收不符（快照）→ 不符", any("已收认定不符" in f for f in r["flags"]),
          r["detail"])
    check("已收不符差额文本", any("差50.00" in f for f in r["flags"]), r["detail"])

    # ---------- 7) 无快照回退：账期窗口 collection ----------
    add_raw("A8", 100.0, "张三100", rid=18)
    add_inv("A8", 100.0)
    add_cd("A8", {"张三": 100.0})
    conn.execute(
        "INSERT INTO collection (invoice_no, receipt_date, amount, person_name, source) "
        "VALUES ('A8','2025-01-10',80.0,'张三','import')")
    r = row_of(rc.build_review_rows(P)[1], "A8")
    check("无快照回退 collection 实收", r["recv_db"] != "—" and "80.00" in r["recv_db"],
          r["recv_db"])
    check("无快照回退已收不符", any("已收认定不符" in f for f in r["flags"]), r["detail"])

    # ---------- 8) 仅源有 / 仅库有 ----------
    add_raw("A9", 100.0, "张三100", rid=19)
    add_inv("B1", 100.0)
    add_cd("B1", {"张三": 100.0})
    b, rows = rc.build_review_rows(P)
    check("库中缺失 → 仅源有", row_of(rows, "A9")["status"] == "仅源有")
    check("非 sheet3 的库中缺失不算需补录（needs_backfill=False）",
          row_of(rows, "A9")["needs_backfill"] is False)
    check("镜表缺失 → 仅库有", row_of(rows, "B1")["status"] == "仅库有")
    check("仅库有行排在源侧之后",
          rows.index(row_of(rows, "B1")) > rows.index(row_of(rows, "A1")))

    # ---------- 9) 已确认异常（旧维度聚合回退） ----------
    conn.execute(
        "INSERT INTO anomaly_note (invoice_no, dim, period, note) "
        "VALUES ('A2','handler',?,'历史备注：手动改过')", (P,))
    r = row_of(rc.build_review_rows(P)[1], "A2")
    check("旧维度备注回退 → 已确认异常", r["status"] == "已确认异常", r["status"])
    check("回退备注内容", r["confirmed_note"] == "历史备注：手动改过", r["confirmed_note"])

    # ---------- 10) dim='merged' 优先 ----------
    conn.execute(
        "INSERT INTO anomaly_note (invoice_no, dim, period, note) "
        "VALUES ('A5','merged',?,'融合后新备注')", (P,))
    r = row_of(rc.build_review_rows(P)[1], "A5")
    check("merged 优先", r["status"] == "已确认异常" and r["confirmed_note"] == "融合后新备注",
          f"{r['status']}/{r['confirmed_note']}")

    # ---------- 11) 同号镜表取末行 ----------
    conn.execute(
        "INSERT INTO raw_ledger (id, sheet_key, sheet_name, row_no, invoice_no, "
        "buyer, amount_num, handler_text, remark, kind, synced, import_batch_id) "
        "VALUES (99, 'sheet1', '已开票已入账', 9, 'A1', '乙公司', 999.0, '', '', "
        "'invoice', 1, 1)")
    r = row_of(rc.build_review_rows(P)[1], "A1")
    check("同号镜表取末行（后者覆盖）", r["buyer_src"] == "乙公司", r["buyer_src"])
    check("同号末行触发金额不符", r["status"] == "不符", r["status"])

    # ---------- 12) 红字无收款 ----------
    add_raw("A10", -50.0, "", rid=20)
    add_inv("A10", -50.0)
    r = row_of(rc.build_review_rows(P)[1], "A10")
    check("红字无收款不报已收不符", r["status"] == "一致",
          f"{r['status']}/{r['detail']}")

    # ---------- 13) 补录（source='manual'）纳入比对口径 ----------
    # 期外票补录进库的是 manual 行；若比对只读 import，该行会永远停在「库中缺失」，
    # 与「补录原票」页（票号对齐即移出）互相矛盾 —— 故库侧口径为
    # source IN ('import','manual')。
    add_raw("A11", 300.0, "张三300", remark="25.1.10", rid=21)
    r = row_of(rc.build_review_rows(P)[1], "A11")
    check("补录前：库中缺失（仅源有，非需补录）",
          r["status"] == "仅源有" and r["needs_backfill"] is False,
          f"{r['status']}/{r['needs_backfill']}")
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('A11','2024-03-01','丙公司',300.0,'manual')")
    conn.execute("INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source) "
                 "VALUES ('A11','张三',300.0,'manual')")
    conn.execute("INSERT INTO collection (invoice_no, receipt_date, amount, person_name, source) "
                 "VALUES ('A11','2025-01-10',300.0,'张三','manual')")
    conn.commit()
    r = row_of(rc.build_review_rows(P)[1], "A11")
    check("补录后：库侧读到 manual 发票（金额/对方）",
          r["amount_db"] == 300.0 and r["buyer_db"] == "丙公司",
          f"{r['amount_db']}/{r['buyer_db']}")
    check("补录后：库侧读到 manual 分摊", "张三" in r["handlers_db"], r["handlers_db"])
    check("补录后：已收认定读 manual 收款", "300.00" in r["recv_db"], r["recv_db"])
    check("补录后：转为一致", r["status"] == "一致", f"{r['status']}/{r['detail']}")

    conn.close()
    print(f"\n{OK} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
