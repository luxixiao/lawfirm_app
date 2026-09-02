"""review_writeback（导入后回写引擎）单元测试 — 内存库。

运行：python tests/test_review_writeback.py（venv python，依赖 xlrd）
覆盖：改金额/购方/经办人/备注/显式收款/清空收款/发票号改名 → 三表落库 +
synced=0 + change_log L1/L2 + 快照；纯改金额不清既有收款；预校验失败整体
回滚；红字不写收款；note 必填。
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine import raw_ledger as rl  # noqa: E402
from app.engine import review_writeback as rw  # noqa: E402
from app import db as _db_mod  # noqa: E402

OK, FAILS = 0, []


class _ConnProxy:
    """引擎 finally 会 close()（应用里每次 get_conn 新建连接）；
    内存库单例连接由本测试持有，close 置空操作防误关。"""
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
    # 复用真实启动路径：SCHEMA + init_db 的全部迁移（person_type/received_override/
    # src_sheet/change_log 快照列等不在 SCHEMA，由迁移补）
    proxy = _ConnProxy(conn)
    _orig = _db_mod.get_conn
    _db_mod.get_conn = lambda: proxy
    _db_mod.init_db()
    _db_mod.get_conn = _orig
    rw.get_conn = lambda: proxy

    def q(sql, *args):
        return conn.execute(sql, args).fetchall()

    def q1(sql, *args):
        return conn.execute(sql, args).fetchone()

    P = "2025-01"
    conn.execute(
        "INSERT INTO import_batch (id, batch_type, period, file_name, status, imported_at) "
        "VALUES (1,'ledger',?,'2025.1台账.xlsx','active', datetime('now','localtime'))", (P,))

    def add_invoice(no, amount, buyer="甲公司", handler="张三100", remark="",
                    kind="invoice", rid=None, synced=1, batch=1):
        rid = rid or conn.execute(
            "SELECT coalesce(max(id),0)+1 FROM raw_ledger").fetchone()[0]
        conn.execute(
            "INSERT INTO raw_ledger (id, sheet_key, sheet_name, row_no, invoice_no, "
            "buyer, amount_raw, amount_num, handler_text, remark, kind, synced, "
            "import_batch_id) VALUES (?, 'sheet1', '已开票已入账', 2, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?)",
            (rid, no, buyer, str(amount), amount, handler, remark, kind, synced, batch))
        if kind == "invoice":
            conn.execute(
                "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, "
                "source, import_batch_id) VALUES (?, '2025-01-05', ?, ?, 'import', ?)",
                (no, buyer, amount, batch))
            if handler:
                from app.importer.parse_handler import parse_handler_column
                for name, amt in parse_handler_column(handler, amount, no):
                    conn.execute(
                        "INSERT INTO charge_detail (invoice_no, person_name, "
                        "billing_amount, source, import_batch_id) VALUES (?,?,?,?,?)",
                        (no, name, amt, "import", batch))
        conn.commit()  # 种子落定：异常路径的 rollback 不得波及未提交种子
        return rid

    # ---------------- 1) 改金额（纯金额，不触收款） ----------------
    rid = add_invoice("A1", 100.0)
    res = rw.apply_edit(P, rid, {"amount_raw": "200"}, "金额笔误修正")
    check("改金额返回 changed 含 amount_raw", "amount_raw" in res["changed"], res["changed"])
    check("invoice.total_amount=200", abs(q1(
        "SELECT total_amount FROM invoice WHERE invoice_no='A1'")["total_amount"] - 200) < 1e-9)
    check("镜表 amount_num=200", q1("SELECT amount_num FROM raw_ledger WHERE id=?",
                                    rid)["amount_num"] == 200)
    check("镜表 synced=0", q1("SELECT synced FROM raw_ledger WHERE id=?",
                              rid)["synced"] == 0)
    l1 = q("SELECT field, old_value, new_value FROM change_log "
           "WHERE table_name='raw_ledger' AND record_id=? AND field='amount_raw'", str(rid))
    check("L1 记录 amount_raw 100→200", l1 and l1[0]["old_value"] == "100.0"
          and l1[0]["new_value"] == "200", str(l1))
    l2 = q("SELECT field FROM change_log WHERE table_name='raw_ledger' "
           "AND record_id=? AND field='(同步)'", str(rid))
    check("L2 (同步) 汇总存在", len(l2) == 1)
    # 纯改金额：既有空收款不被触碰 → 仍 0 条
    check("纯改金额不动收款", q1("SELECT count(*) FROM collection "
                              "WHERE invoice_no='A1'")[0] == 0)
    check("charge_detail 保持原分摊", q1("SELECT count(*) FROM charge_detail "
                                       "WHERE invoice_no='A1'")[0] == 1)

    # ---------------- 2) 改购方 ----------------
    rid = add_invoice("A2", 100.0)
    rw.apply_edit(P, rid, {"buyer": "乙公司"}, "购方更正")
    check("invoice.buyer=乙公司", q1("SELECT buyer FROM invoice WHERE invoice_no='A2'")["buyer"] == "乙公司")
    check("镜表 buyer=乙公司", q1("SELECT buyer FROM raw_ledger WHERE id=?", rid)["buyer"] == "乙公司")

    # ---------------- 3) 改经办人 ----------------
    rid = add_invoice("A3", 100.0)
    rw.apply_edit(P, rid, {"handler_text": "张三60、李四40"}, "拆分更正")
    cds = q("SELECT person_name, billing_amount FROM charge_detail "
            "WHERE invoice_no='A3' ORDER BY person_name")
    check("charge_detail 重算为 2 人", len(cds) == 2, str([dict(c) for c in cds]))
    check("分摊金额正确", {c["person_name"]: c["billing_amount"] for c in cds}
          == {"张三": 60.0, "李四": 40.0})

    # ---------------- 4) 改备注（25.12.10 纯日期）→ 收款自动落库 ----------------
    rid = add_invoice("A4", 100.0, remark="")
    rw.apply_edit(P, rid, {"remark": "25.12.10"}, "补收款日期")
    colls = q("SELECT amount, receipt_date FROM collection WHERE invoice_no='A4'")
    check("备注纯日期 → collection 1 条全额", len(colls) == 1
          and abs(colls[0]["amount"] - 100.0) < 1e-9, str([dict(c) for c in colls]))
    snap = q1("SELECT actual_json FROM received_snapshot WHERE invoice_no='A4'")
    check("received_snapshot actual 同步", snap and json.loads(snap["actual_json"])["total"] == 100.0)

    # ---------------- 5) 显式收款覆盖 + 清空 ----------------
    rid = add_invoice("A5", 100.0, remark="25.11.30")
    rw.apply_edit(P, rid, {"receipts": [("张三", 80.0, "2025-12"),
                                        ("李四", 20.0, "2025-12")]}, "逐人收款修正")
    colls = q("SELECT person_name, amount FROM collection WHERE invoice_no='A5' "
              "ORDER BY person_name")
    check("显式收款 2 条", len(colls) == 2, str([dict(c) for c in colls]))
    rw.apply_edit(P, rid, {"receipts": []}, "实际未收款，清空")
    check("receipts=[] 清空收款", q1("SELECT count(*) FROM collection "
                                   "WHERE invoice_no='A5'")[0] == 0)

    # ---------------- 6) 发票号改名级联 ----------------
    rid = add_invoice("A6", 100.0)
    conn.execute("INSERT INTO collection (invoice_no, amount, receipt_date, person_name, "
                 "source, import_batch_id) VALUES ('A6',100,'2025-01','','import',1)")
    conn.commit()
    rw.apply_edit(P, rid, {"invoice_no": "A6-NEW"}, "发票号更正")
    check("invoice 改名", q1("SELECT 1 FROM invoice WHERE invoice_no='A6-NEW'") is not None)
    check("旧号无 invoice", q1("SELECT 1 FROM invoice WHERE invoice_no='A6'") is None)
    check("collection 级联改名", q1("SELECT count(*) FROM collection "
                                   "WHERE invoice_no='A6-NEW'")[0] == 1)
    check("charge_detail 级联改名", q1("SELECT count(*) FROM charge_detail "
                                      "WHERE invoice_no='A6-NEW'")[0] == 1)

    # ---------------- 7) 红字不写收款 ----------------
    rid = add_invoice("A7", -100.0, handler="")
    rw.apply_edit(P, rid, {"remark": "25.12.10"}, "红字备注修正")
    check("红字不写 collection", q1("SELECT count(*) FROM collection "
                                   "WHERE invoice_no='A7'")[0] == 0)
    check("红字 invoice.total_amount=-100", q1(
        "SELECT total_amount FROM invoice WHERE invoice_no='A7'")["total_amount"] == -100.0)

    # ---------------- 8) 校验失败整体回滚 ----------------
    rid = add_invoice("A8", 100.0)
    try:
        rw.apply_edit(P, rid, {"handler_text": "张三80"}, "合计不符")
        check("经办人合计≠总额被拒", False)
    except ValueError:
        check("经办人合计≠总额被拒", True)
    check("回滚：金额未变", q1("SELECT amount_num FROM raw_ledger WHERE id=?",
                              rid)["amount_num"] == 100.0)
    check("回滚：无 change_log", q1("SELECT count(*) FROM change_log WHERE record_id=?",
                                   str(rid))[0] == 0)

    try:
        rw.apply_edit(P, rid, {"amount_raw": "abc"}, "金额非法")
        check("金额无法解析被拒", False)
    except ValueError:
        check("金额无法解析被拒", True)

    # 改号撞已有发票 → 拒绝
    add_invoice("EXIST", 50.0, handler="张三50")
    try:
        rw.apply_edit(P, rid, {"invoice_no": "EXIST"}, "撞号")
        check("改号撞已有发票被拒", False)
    except ValueError:
        check("改号撞已有发票被拒", True)

    # note 必填
    try:
        rw.apply_edit(P, rid, {"buyer": "X"}, "  ")
        check("修改原因必填", False)
    except ValueError:
        check("修改原因必填", True)

    # ---------------- 9) 无变化 = 无写入 ----------------
    rid = add_invoice("A9", 100.0)
    res = rw.apply_edit(P, rid, {"buyer": "甲公司"}, "无实际变化")
    check("无实际变化返回空 changed", res["changed"] == [], res["changed"])
    check("无变化仍置 synced=0（回写意图留痕）",
          q1("SELECT synced FROM raw_ledger WHERE id=?", rid)["synced"] == 0)

    # ---------------- 10) 预收款行拒绝 ----------------
    rid_pp = add_invoice("PP1", 500.0, kind="prepayment", handler="", buyer="丙公司")
    try:
        rw.apply_edit(P, rid_pp, {"buyer": "丁公司"}, "改预收款")
        check("预收款行被拒绝", False)
    except ValueError:
        check("预收款行被拒绝", True)

    # ---------------- 11) rewrite_collection=True 强制按备注重写 ----------------
    rid = add_invoice("R1", 100.0, remark="25.12.10")
    rw.apply_edit(P, rid, {"receipts": [("张三", 80.0, "2025-12"),
                                        ("李四", 20.0, "2025-12")]}, "逐人收款")
    check("显式收款 2 条（预置）", q1("SELECT count(*) FROM collection "
                                   "WHERE invoice_no='R1'")[0] == 2)
    rw.apply_edit(P, rid, {"buyer": "乙公司"}, "仅改购方")  # 备注未变 + 无 receipts → 不重写
    check("备注未变不重写收款（防清空）", q1("SELECT count(*) FROM collection "
                                        "WHERE invoice_no='R1'")[0] == 2)
    rw.apply_edit(P, rid, {"buyer": "甲公司"}, "强制按备注重写",
                  rewrite_collection=True)
    colls = q("SELECT amount FROM collection WHERE invoice_no='R1'")
    check("rewrite_collection 撤销显式收款→按纯日期备注全额 1 条",
          len(colls) == 1 and abs(colls[0]["amount"] - 100.0) < 1e-9,
          str([dict(c) for c in colls]))

    # ---------------- 12) restore_from_archive 单行还原 ----------------
    from app.importer import importer as _imp  # noqa: E402
    rid = add_invoice("R2", 100.0, remark="25.11.30")  # 原件坐标：已开票已入账 第2行
    rw.apply_edit(P, rid, {"amount_raw": "200", "handler_text": "张三200"},
                  "手改金额200")
    rw.apply_edit(P, rid, {"receipts": [("张三", 200.0, "2025-12")]}, "手改收款")
    check("还原前已手工修订 synced=0",
          q1("SELECT synced FROM raw_ledger WHERE id=?", rid)["synced"] == 0)

    header = ["序号", "开票日期", "金额", "发票号码", "对方", "经办人", "备注"]
    raw_row = ["1", "2025-01-05", "100", "R2", "甲公司", "张三100", "25.11.30"]

    def _fake_parse(path, period):
        return {"invoices": [{
            "sheet_name": "已开票已入账", "row_no": 2, "invoice_no": "R2",
            "buyer": "甲公司", "total_amount": 100.0, "handler_text": "张三100",
            "remark_raw": "25.11.30", "case_no": "",
            "raw_row": raw_row, "header": header,
        }], "prepayments": [], "problems": []}

    _imp.parse_ledger_file = _fake_parse
    res = rw.restore_from_archive(P, rid, "data/archive/fake.xlsx")
    check("还原成功标记", res.get("restored") is True, str(res))
    check("还原后金额回 100",
          q1("SELECT amount_num, synced FROM raw_ledger WHERE id=?", rid)["amount_num"] == 100.0)
    check("还原后 synced=1",
          q1("SELECT synced FROM raw_ledger WHERE id=?", rid)["synced"] == 1)
    check("还原后 invoice.total_amount=100", abs(q1(
        "SELECT total_amount FROM invoice WHERE invoice_no='R2'")["total_amount"] - 100.0) < 1e-9)
    colls = q("SELECT amount FROM collection WHERE invoice_no='R2'")
    check("还原后收款按原备注纯日期全额 1 条",
          len(colls) == 1 and abs(colls[0]["amount"] - 100.0) < 1e-9,
          str([dict(c) for c in colls]))

    # 还原找不到原件（问题行/无坐标）
    rid_no = add_invoice("R3", 50.0, handler="张三50", remark="")
    conn.execute("UPDATE raw_ledger SET sheet_name='', row_no=0 WHERE id=?", (rid_no,))
    conn.commit()
    try:
        rw.restore_from_archive(P, rid_no, "data/archive/fake.xlsx")
        check("无原件坐标被拒", False)
    except ValueError:
        check("无原件坐标被拒", True)
    # 存档解析无此行（坐标 99 不在假解析结果里）
    rid_miss = add_invoice("R4", 60.0, handler="张三60", remark="")
    conn.execute("UPDATE raw_ledger SET row_no=99 WHERE id=?", (rid_miss,))
    conn.commit()
    try:
        rw.restore_from_archive(P, rid_miss, "data/archive/fake.xlsx")
        check("存档无该行解析结果被拒", False)
    except ValueError:
        check("存档无该行解析结果被拒", True)

    conn.close()
    print(f"\n{OK} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
