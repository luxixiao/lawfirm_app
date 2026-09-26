"""阶段 3（B2）单元测试 —— 补录随台账**同一事务**入库（内存库）。

运行：python tests/test_backfill_pending.py

覆盖：
- B2a `build_backfill`：纯计算 + 校验（票号/日期必填、至少一名经办人、须在花名册、
  已收不超价税合计），**不写库**；返回规范化 payload（charge / collections）
- B2a `apply_backfill`：用**传入的 conn** 写入、**不 commit**（rollback 即消失）；
  非 manual 票守卫（绝不把销项票静默改成 manual）
- `save_backfill`：独立补录页立即写库（行为回归，含 import_batch_id 留空）
- B2b `commit_ledger_import(backfills=...)`：同一事务写
  invoice(source='manual') + charge_detail + collection；
  `import_batch_id` 留空、**不写 received_snapshot**、不写 raw_ledger 镜像
- **原子性**：注入写库故障 → 台账与补录**都回滚**（同进同出）
- B2d/B2e `validate_ledger_before_write`：补录重复票号 / 已在库 / 与本批 sheet1-2 撞号
- B2c `create=False`：已入库票只追加收款（与 A10 同一通道）；超额 → 记 over_collected 且不写
- 阶段 4-1：补录 ↔ A10 **去重**（同票号只写一套，不因翻倍被判超额）+ A10 排在补录之后
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import backfill as bf  # noqa: E402
from app.engine import backfill_module as bm  # noqa: E402
from app.engine import raw_ledger as rl  # noqa: E402
from app.importer import importer as imp  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


class _ConnProxy:
    """引擎 finally 会 close()；内存库单例连接由本测试持有，close 置空操作。"""

    def __init__(self, c):
        self._c = c

    def execute(self, *a, **k):
        return self._c.execute(*a, **k)

    def executescript(self, *a, **k):
        return self._c.executescript(*a, **k)

    def commit(self):
        self._c.commit()

    def close(self):
        pass

    def __getattr__(self, n):
        return getattr(self._c, n)


HEADER = ["序号", "开票日期", "发票号码", "对方", "金额", "经办人", "备注", "案号"]


def _raise_value(fn, *a, **k):
    """调用 fn 期望抛 ValueError/ImportError_，返回错误文本（否则返回 None）。"""
    try:
        fn(*a, **k)
    except Exception as e:  # noqa: BLE001
        return str(e)
    return None


def main() -> int:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # SCHEMA 只含建表；person_type / src_sheet / src_row / received_override / hire_month
    # 由 init_db 的迁移补列（见 app/db.py），内存库需照做（与 test_deferred_sheet3 同）。
    for stmt in (
        "ALTER TABLE charge_detail ADD COLUMN person_type TEXT DEFAULT ''",
        "ALTER TABLE charge_detail ADD COLUMN received_override REAL DEFAULT NULL",
        "ALTER TABLE charge_detail ADD COLUMN src_sheet TEXT DEFAULT ''",
        "ALTER TABLE charge_detail ADD COLUMN src_row INTEGER DEFAULT 0",
        "ALTER TABLE collection ADD COLUMN src_sheet TEXT DEFAULT ''",
        "ALTER TABLE collection ADD COLUMN src_row INTEGER DEFAULT 0",
        "ALTER TABLE invoice ADD COLUMN src_sheet TEXT DEFAULT ''",
        "ALTER TABLE invoice ADD COLUMN src_row INTEGER DEFAULT 0",
        "ALTER TABLE staff ADD COLUMN hire_month TEXT DEFAULT ''",
        # 去写死（Plan A）：角色列（复刻 init_db 迁移）
        "ALTER TABLE staff_type_def ADD COLUMN role_code TEXT NOT NULL DEFAULT 'other'",
    ):
        conn.execute(stmt)
    # 去写死（Plan A）：role_def 表 + 种子（内存库手工复刻）
    from app.engine import staff_type as _st
    _st.ensure_roles(conn)
    proxy = _ConnProxy(conn)

    # 四个模块的连接统一换成代理：library_invoice_nos() 也走 proxy（不传 conn 时）
    for mod in (rl, bm, imp, bf):
        mod.get_conn = lambda: proxy

    imp._auto_snapshot = lambda *a, **k: None  # P2-3 起新签名 (batch_type, period)
    imp._archive_file = lambda src, bt, period: ""

    # 去写死（Plan A）：建员工类型 + 角色映射（复刻 init_db 的按名迁移，仅 fixture）
    from app.engine import staff_type as _st
    for _n, _c in (("合伙", "partner"), ("聘用", "employee"), ("兼职", "parttime")):
        if _st.get_type(_n, conn) is None:
            _st.add_type(_n, conn=conn)
        _st.set_role_code(_n, _c, conn=conn)

    for nm, t in (("周立生", "聘用"), ("陈娟", "兼职"), ("胡坚", "合伙")):
        conn.execute("INSERT INTO staff (name, staff_type, is_active) VALUES (?,?,1)", (nm, t))
    conn.commit()

    def bf_form(no="BF-1", date="2024-03-01", buyer="甲", total=10000.0, handlers=None):
        return {
            "invoice_no": no, "invoice_date": date, "buyer": buyer, "total_amount": total,
            "handlers": handlers if handlers is not None else [
                {"name": "周立生", "billing": 6000.0, "received": 3000.0, "date": "2024-06-01"},
                {"name": "陈娟", "billing": 4000.0, "received": 0.0, "date": ""},
            ],
        }

    def _inv_sql(no):
        return conn.execute("SELECT source, import_batch_id FROM invoice WHERE invoice_no=?",
                            (no,)).fetchone()

    def _coll(no):
        return [dict(r) for r in conn.execute(
            "SELECT amount, receipt_date, person_name, source, import_batch_id, note "
            "FROM collection WHERE invoice_no=? ORDER BY id", (no,))]

    # ============================================================ A) build_backfill（不写库）
    err = _raise_value(bm.build_backfill, proxy, bf_form(no=""))
    check("A1 build：票号必填", err is not None and "发票号码不能为空" in err, str(err))

    err = _raise_value(bm.build_backfill, proxy, bf_form(date="  "))
    check("A2 build：开票日期必填", err is not None and "开票日期不能为空" in err, str(err))

    err = _raise_value(bm.build_backfill, proxy, bf_form(handlers=[]))
    check("A3 build：至少一名经办人",
          err is not None and "至少要填写一名经办人" in err, str(err))

    err = _raise_value(bm.build_backfill, proxy, bf_form(handlers=[
        {"name": "查无此人", "billing": 100.0, "received": 0.0, "date": ""}]))
    check("A4 build：经办人须在花名册",
          err is not None and "不在职工花名册" in err, str(err))

    err = _raise_value(bm.build_backfill, proxy, bf_form(total=100.0, handlers=[
        {"name": "周立生", "billing": 100.0, "received": 200.0, "date": "2024-06-01"}]))
    check("A5 build：已收超价税合计被拦",
          err is not None and "超过价税合计" in err, str(err))

    payload = bm.build_backfill(proxy, bf_form())
    # 去写死（Plan A）：person_type 存**角色码**（显示层才翻中文），不再是中文标签
    check("A6 build：返回规范化 payload（charge 按人聚合 + person_type=角色码）",
          payload["charge"] == [("周立生", 6000.0, "employee"), ("陈娟", 4000.0, "parttime")],
          str(payload["charge"]))
    check("A7 build：collections 只含已收>0 且有日期的行",
          payload["collections"] == [("周立生", 3000.0, "2024-06-01")],
          str(payload["collections"]))
    check("A8 build：**不写库**", _inv_sql("BF-1") is None)

    # ============================================================ B) apply_backfill（不 commit）
    bm.apply_backfill(proxy, payload)
    check("B1 apply：同一连接内可见 invoice(source='manual')",
          _inv_sql("BF-1") is not None and _inv_sql("BF-1")["source"] == "manual")
    check("B2 apply：import_batch_id 留空", _inv_sql("BF-1")["import_batch_id"] is None)
    proxy.rollback()
    check("B3 apply：**不 commit** —— 回滚后消失", _inv_sql("BF-1") is None
          and _coll("BF-1") == [])

    # 非 manual 票守卫：已在库的 import 票不得被补录覆盖
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('IMPORT-1', '2024-01-05', '乙', 500.0, 'import')")
    conn.commit()
    err = _raise_value(bm.apply_backfill, proxy,
                       bm.build_backfill(proxy, bf_form(
                           no="IMPORT-1", total=500.0,
                           handlers=[{"name": "周立生", "billing": 500.0,
                                      "received": 0.0, "date": ""}])))
    check("B4 apply：非 manual 票守卫（不把销项票改成 manual）",
          err is not None and "不能以补录方式覆盖" in err, str(err))
    check("B5 apply：守卫触发时票面未被改动",
          _inv_sql("IMPORT-1")["source"] == "import")
    proxy.rollback()

    # ============================================================ C) save_backfill（立即写库）
    conn.execute("DELETE FROM invoice WHERE invoice_no='BF-2'")
    conn.commit()
    bm.save_backfill(bf_form(no="BF-2", date="2024-04-01"))
    check("C1 save_backfill：独立页立即写库（自己 commit）", _inv_sql("BF-2") is not None)
    check("C2 save_backfill：collection 一笔 + note=补录",
          len(_coll("BF-2")) == 1 and _coll("BF-2")[0]["note"] == "补录",
          str(_coll("BF-2")))
    check("C3 save_backfill：不写 received_snapshot",
          conn.execute("SELECT COUNT(*) FROM received_snapshot WHERE invoice_no='BF-2'"
                       ).fetchone()[0] == 0)

    # ============================================================ D) commit 同事务写补录
    P = "2025-03"
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('S3', '2025-03-10', '销项甲', 100.0, 'import')")
    conn.commit()
    d_ok = {
        "invoices": [], "deferred": [], "prepayments": [], "problems": [],
        "sheet_totals": {"sheet1": 100.0}, "sheet12_total": 100.0,
        "backfills": [bf_form(no="BF-9", date="2024-05-06")],
    }
    r = imp.commit_ledger_import(d_ok, P, "2025.3台账.xlsx")
    check("D1 commit：返回 backfill_count", r.get("backfill_count") == 1, str(r.get("backfill_count")))
    check("D2 commit：补录票落库为 source='manual'",
          _inv_sql("BF-9") is not None and _inv_sql("BF-9")["source"] == "manual")
    check("D3 commit：import_batch_id 留空（不属该批次）",
          _inv_sql("BF-9")["import_batch_id"] is None,
          str(_inv_sql("BF-9")["import_batch_id"]))
    cds = {r0["person_name"]: (r0["billing_amount"], r0["person_type"]) for r0 in conn.execute(
        "SELECT person_name, billing_amount, person_type FROM charge_detail WHERE invoice_no='BF-9'")}
    check("D4 commit：charge_detail 分摊 + person_type=角色码",
          cds == {"周立生": (6000.0, "employee"), "陈娟": (4000.0, "parttime")}, str(cds))
    cols = _coll("BF-9")
    check("D5 commit：collection 一笔 manual/补录",
          len(cols) == 1 and cols[0]["source"] == "manual" and cols[0]["note"] == "补录",
          str(cols))
    check("D6 commit：**不写 received_snapshot**",
          conn.execute("SELECT COUNT(*) FROM received_snapshot WHERE invoice_no='BF-9'"
                       ).fetchone()[0] == 0)
    check("D7 commit：补录票**不写** raw_ledger 镜像（镜像只属台账文件 1:1）",
          conn.execute("SELECT COUNT(*) FROM raw_ledger WHERE invoice_no='BF-9'"
                       ).fetchone()[0] == 0)

    # ============================================================ E) 原子性（注入中途故障）
    P2 = "2025-04"
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('S4', '2025-04-10', '销项乙', 100.0, 'import')")
    conn.commit()

    def _row(no, date, buyer, amount, name, sheet="sheet1"):
        return {"sheet": sheet, "sheet_name": "已开票已入账", "row_no": 2,
                "header": HEADER, "raw_row": ["1", date, no, buyer, str(amount), name, "", ""],
                "invoice_no": no, "invoice_date": date, "buyer": buyer,
                "total_amount": amount, "handlers": [(name, amount)],
                "handler_text": name, "remark_raw": "",
                "remark": {"receipts": [], "remaining": None, "pure_date": None,
                           "is_red_remark": False, "is_red_off": False},
                "case_no": "", "is_red": False}

    d_atomic = {
        "invoices": [_row("LNEW", "2025-04-11", "新票", 100.0, "周立生")],
        "deferred": [], "prepayments": [], "problems": [],
        "sheet_totals": {"sheet1": 100.0}, "sheet12_total": 100.0,
        "backfills": [bf_form(no="BF-A", date="2024-05-01"),
                      bf_form(no="BF-B", date="2024-05-02")],
    }
    _real_apply = bm.apply_backfill
    _n = {"c": 0}

    def _boom(conn2, payload2):
        _n["c"] += 1
        if _n["c"] == 2:                      # 台账已写完、第 2 条补录写入时炸
            raise RuntimeError("注入的写库故障")
        return _real_apply(conn2, payload2)

    bm.apply_backfill = _boom
    try:
        err = _raise_value(imp.commit_ledger_import, d_atomic, P2, "2025.4台账.xlsx")
    finally:
        bm.apply_backfill = _real_apply
    check("E1 原子性：故障如实抛出", err is not None and "注入的写库故障" in err, str(err))
    check("E2 原子性：台账普通票**被回滚**（未落库）", _inv_sql("LNEW") is None)
    check("E3 原子性：第 1 条补录**也被回滚**（同进同出）", _inv_sql("BF-A") is None)
    check("E4 原子性：本批 import_batch 未留下（active 批次不存在）",
          conn.execute("SELECT COUNT(*) FROM import_batch WHERE period=? AND batch_type='ledger'",
                       (P2,)).fetchone()[0] == 0)
    check("E5 原子性：raw_ledger 无本批镜像行",
          conn.execute("SELECT COUNT(*) FROM raw_ledger WHERE invoice_no='LNEW'"
                       ).fetchone()[0] == 0)

    # ============================================================ F) 写前校验（B2d/B2e）
    def _vdata(**kw):
        d = {"invoices": [], "deferred": [], "prepayments": [], "problems": [],
             "sheet_totals": {}, "sheet12_total": 0.0}
        d.update(kw)
        return d

    err = imp.validate_ledger_before_write(
        _vdata(backfills=[bf_form(no="DUP-1"), bf_form(no="DUP-1")]), "2026-01", set())
    check("F1 校验：同批重复票号 → 报错并指出次数",
          err is not None and "出现 2 次" in err, str(err))

    err = imp.validate_ledger_before_write(
        _vdata(backfills=[bf_form(no="S3")]), "2026-01", {"S3"})
    check("F2 校验：补录票号已在库 → 无需补录",
          err is not None and "已在库中" in err, str(err))

    err = imp.validate_ledger_before_write(
        _vdata(invoices=[_row("X1", "2026-01-02", "同批", 100.0, "周立生")],
               backfills=[bf_form(no="X1")]), "2026-01", set())
    check("F3 校验：补录票号与本批 sheet1/2 撞号 → 该票随台账入库",
          err is not None and "sheet1/2" in err, str(err))

    err = imp.validate_ledger_before_write(
        _vdata(backfills=[bf_form(no="BAD-1", handlers=[])]), "2026-01", set())
    check("F4 校验：补录经办人缺失 → 带票号前缀",
          err is not None and "补录发票 BAD-1" in err and "至少要填写一名经办人" in err, str(err))

    check("F5 校验：无 backfills → 不受影响",
          imp.validate_ledger_before_write(_vdata(), "2026-01", set()) is None)

    # ============================================================ G) create=False（只追加收款）
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('EX1', '2024-01-05', '乙', 1000.0, 'import')")
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('S5', '2025-05-10', '销项丙', 100.0, 'import')")
    conn.commit()
    P3 = "2025-05"
    d_add = _vdata(sheet_totals={"sheet1": 100.0}, sheet12_total=100.0,
                   backfills=[{"invoice_no": "EX1", "create": False,
                               "collections": [("周立生", 300.0, "2025-05-06")]}])
    check("G1 校验：create=False 条目通过（不跑 build_backfill）",
          imp.validate_ledger_before_write(d_add, P3, set()) is None,
          str(imp.validate_ledger_before_write(d_add, P3, set())))
    r3 = imp.commit_ledger_import(d_add, P3, "2025.5台账.xlsx")
    cols = _coll("EX1")
    check("G2 create=False：只**追加**收款（source='import' + 本批 batch_id）",
          len(cols) == 1 and cols[0]["amount"] == 300.0 and cols[0]["source"] == "import"
          and cols[0]["import_batch_id"] == r3["batch_id"], str(cols))
    check("G3 create=False：不动票面（source 仍 import）", _inv_sql("EX1")["source"] == "import")

    # 超额：累计 300 + 800 > 1000 → 不写 + 记 over_collected
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('S6', '2025-06-10', '销项丁', 100.0, 'import')")
    conn.commit()
    P4 = "2025-06"
    d_over = _vdata(sheet_totals={"sheet1": 100.0}, sheet12_total=100.0,
                    backfills=[{"invoice_no": "EX1", "create": False,
                                "collections": [("周立生", 800.0, "2025-06-06")]}])
    r4 = imp.commit_ledger_import(d_over, P4, "2025.6台账.xlsx")
    over = r4.get("over_collected") or []
    check("G4 create=False 超额 → 记入 over_collected",
          any(o["invoice_no"] == "EX1" for o in over), str(over))
    check("G5 create=False 超额 → 不写入（仍只有 1 笔）", len(_coll("EX1")) == 1,
          str(_coll("EX1")))
    check("G6 超额文案可读（剩余应收 0 元，已收款完成）",
          bool(over) and "已收款完成" in over[0]["message"], str(over[:1]))

    # ============================================================ H) 阶段 4-1：补录 ↔ A10 去重
    # 背景：补录写 source='manual'（apply_backfill），A10「确认收款」写 source='import'
    # （_append_receipts_to_existing）。若同一票号两边都写 → 同一笔收款两套并存 →
    # 全来源合计翻倍 → 被超额校验判成「台账信息错误」（假报错，整行拒写）。
    # 口径（阶段 4-1）：本批 backfills 覆盖的票号，A10 一律跳过（补录是显式指令，优先）。
    # 另：A10 必须排在 _write_backfills 之后，否则「不在库就跳过」会把刚补录出来的票误伤。
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('S7', '2025-07-10', '销项戊', 100.0, 'import')")
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('EX2', '2024-02-05', '已入库票', 1000.0, 'import')")
    conn.commit()
    P5 = "2025-07"
    d_dedup = _vdata(
        sheet_totals={"sheet1": 100.0}, sheet12_total=100.0,
        deferred=[
            # ① 与补录同票号：receipt_confirmed 也置了 → 只应保留补录那一套（manual）
            {"invoice_no": "BF-DUP", "receipt_confirmed": True,
             "split_receipts": [("陈娟", 2000.0, "2024-06-02")],
             "sheet": "sheet3", "sheet_name": "应收账款", "row_no": 2},
            # ② 真·已在库票：A10 必须照常追加（证明 A10 仍在跑、去重只限补录票号）
            {"invoice_no": "EX2", "receipt_confirmed": True,
             "split_receipts": [("周立生", 400.0, "2025-07-08")],
             "sheet": "sheet3", "sheet_name": "应收账款", "row_no": 3},
        ],
        backfills=[bf_form(no="BF-DUP", date="2024-07-01")],
    )
    r5 = imp.commit_ledger_import(d_dedup, P5, "2025.7台账.xlsx")
    dup_cols = _coll("BF-DUP")
    check("H1 去重：补录票收款只写**一套**（manual，不叠加 A10 的 import）",
          len(dup_cols) == 1 and dup_cols[0]["source"] == "manual"
          and dup_cols[0]["amount"] == 3000.0, str(dup_cols))
    check("H2 去重：该票无 import 来源的重复收款",
          not any(c["source"] == "import" for c in dup_cols), str(dup_cols))
    check("H3 去重：补录票不被计入 over_collected",
          not any(o["invoice_no"] == "BF-DUP" for o in (r5.get("over_collected") or [])),
          str(r5.get("over_collected")))
    ex2_cols = _coll("EX2")
    check("H4 A10 仍在补录之后照常追加（真·已在库票，source='import' + 本批 batch_id）",
          len(ex2_cols) == 1 and ex2_cols[0]["source"] == "import"
          and ex2_cols[0]["amount"] == 400.0
          and ex2_cols[0]["import_batch_id"] == r5["batch_id"], str(ex2_cols))

    # 极端：两套收款相加会超额（3000 manual + 8000 A10 > 10000）→ 有守卫则无假报错
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('S8', '2025-08-10', '销项己', 100.0, 'import')")
    conn.commit()
    P6 = "2025-08"
    d_dedup2 = _vdata(
        sheet_totals={"sheet1": 100.0}, sheet12_total=100.0,
        deferred=[{"invoice_no": "BF-DUP2", "receipt_confirmed": True,
                   "split_receipts": [("陈娟", 8000.0, "2024-06-03")],
                   "sheet": "sheet3", "sheet_name": "应收账款", "row_no": 4}],
        backfills=[bf_form(no="BF-DUP2", date="2024-07-02")],
    )
    r6 = imp.commit_ledger_import(d_dedup2, P6, "2025.8台账.xlsx")
    dup2 = _coll("BF-DUP2")
    check("H5 去重：两套相加本会超额 → 仍不产生假报错（over_collected 为空）",
          not (r6.get("over_collected") or []), str(r6.get("over_collected")))
    check("H6 去重：该票仍只有补录那一套收款",
          len(dup2) == 1 and dup2[0]["source"] == "manual" and dup2[0]["amount"] == 3000.0,
          str(dup2))

    # ============================================================ 汇总
    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + " | ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
