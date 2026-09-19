"""批 3-1 单元测试 —— 导入时「确认」留痕（内存库）。

运行：python tests/test_import_confirm.py

为什么需要（背景）
------------------
四态里有两类疑问**只能靠人点「确认」消掉**：`import_confidence.evaluate()` 的兜底低置信、
`red_orig_diff()` 的红字 ⇄ 蓝字不一致。而复核页的 `_confirmed` 只是**内存下标集合**，
库里零痕迹 → 批 1b 的「导入后」模式从 `raw_ledger` 重推四态时必然把它们**重新报成
「待确认」**，而 post 模式又隐藏了「确认」按钮 ⇒ **用户点不掉的假待办**。
落点：复用既有 `anomaly_note` 表，新增独立 `dim='import_confirm'`（**零 schema**）。

覆盖
----
A) 引擎 `app.engine.import_confirm`
   - save/load 往返；空票号跳过；空 items 不炸
   - `clear_confirmations` **只清自己的 dim** —— 旧「导入后」页的人工备注
     （`dim='merged'` / `handler`）**绝不动**（否则批 4 删旧页前的历史备注会被台账导入抹掉）
   - `load_confirmations`：本 dim 优先，缺失时回退旧维度（与 `review_compare` 同口径）
B) 写侧接线 `commit_ledger_import`
   - `data["confirmations"]` 随台账**同一事务**落 `anomaly_note`，返回 `confirmation_count`
   - 覆盖式重导同账期：**先清后写**（上次的确认对新一批已不适用，必须失效）
   - `rollback_batch`：清本批账期的留痕，且**不碰**旧页人工备注
C) 读侧 `rebuild_period_data`
   - `confirmations` 随 data 带出（供 post 模式压制已确认疑问）
   - 无 active 台账批次 → 空字典（骨架字段齐全，不抛异常）
D) 批 3-2b：修改字段留痕（dim=`import_edit`，与确认留痕同构）
   - save/load 往返；空票号/空字段列表跳过；幂等
   - 与 `import_confirm` dim 并存互不覆盖；`clear_edit_hints` 只清自己
   - `commit_ledger_import` 同事务写 + 覆盖式重导先清后写；返回 `edit_hint_count`
   - `rebuild_period_data` 带出 `edit_hints`
E) 批 3-2c：补录票号留痕（dim=`import_backfill`，第三只同族 dim）
   - save/load 往返（载荷只有票号 → 返回 `set`）；空/空白票号跳过；幂等
   - 三个 dim 并存互不覆盖；`clear_backfill_hints` 只清自己
   - `commit_ledger_import` 同事务写 + 覆盖式重导先清后写；返回 `backfill_hint_count`
   - `rebuild_period_data` 带出 `backfilled`
   - `rollback_batch` **三个 dim 一起清**（批 3-2b 曾漏了 `import_edit`，本批一并收口）
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import backfill as bf  # noqa: E402
from app.engine import backfill_module as bm  # noqa: E402
from app.engine import import_confirm as IC  # noqa: E402
from app.engine import raw_ledger as rl  # noqa: E402
from app.engine import review_rebuild as RB  # noqa: E402
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


def main() -> int:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # SCHEMA 只含建表；person_type / src_sheet / src_row / received_override / hire_month
    # 由 init_db 的迁移补列（见 app/db.py），内存库需照做（与 test_backfill_pending 同）。
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
    ):
        conn.execute(stmt)
    proxy = _ConnProxy(conn)

    # 各模块的连接统一换成代理（不传 conn 时也走内存库）
    for mod in (rl, bm, imp, bf, IC, RB):
        mod.get_conn = lambda: proxy

    imp._auto_snapshot = lambda: None
    imp._archive_file = lambda src, bt, period: ""

    for nm, t in (("周立生", "聘用"), ("陈娟", "兼职"), ("胡坚", "合伙")):
        conn.execute("INSERT INTO staff (name, staff_type, is_active) VALUES (?,?,1)", (nm, t))
    conn.commit()

    def _dims(period):
        return {r["invoice_no"]: r["dim"] for r in conn.execute(
            "SELECT invoice_no, dim FROM anomaly_note WHERE period=?", (period,))}

    def _active_batch(period):
        row = conn.execute(
            "SELECT id FROM import_batch WHERE batch_type='ledger' AND period=? AND status='active'",
            (period,)).fetchone()
        return row["id"] if row else None

    # ============================================================ A) 引擎往返
    P = "2025-03"
    n = IC.save_confirmations(proxy, P, [
        {"invoice_no": "A1", "note": "无经办人"},
        {"invoice_no": "A2", "note": "红字与蓝字原票不一致（总金额）"},
        {"invoice_no": "", "note": "空票号应跳过"},
        {"invoice_no": "  ", "note": "空白票号应跳过"},
    ])
    check("A1 save：写入 2 条（空/空白票号跳过）", n == 2, str(n))
    check("A2 load：票号 → 备注",
          IC.load_confirmations(P, proxy) == {
              "A1": "无经办人", "A2": "红字与蓝字原票不一致（总金额）"},
          str(IC.load_confirmations(P, proxy)))
    check("A3 save：items=None 不炸且返回 0", IC.save_confirmations(proxy, P, None) == 0)
    check("A3 save：同票同账期幂等（INSERT OR REPLACE）",
          IC.save_confirmations(proxy, P, [{"invoice_no": "A1", "note": "改过的备注"}]) == 1
          and IC.load_confirmations(P, proxy)["A1"] == "改过的备注"
          and len([1 for r in conn.execute(
              "SELECT 1 FROM anomaly_note WHERE period=? AND invoice_no='A1'", (P,))]) == 1)

    # 旧「导入后」页的人工备注（dim='merged'/'handler'）—— 读取时作为回退来源，
    # 清除时**绝不能碰**（那是人在导入后手工标记的异常备注，与本留痕两回事）。
    conn.execute("INSERT INTO anomaly_note (invoice_no, dim, period, note, confirmed_at) "
                 "VALUES ('M1','merged',?, '人工备注', datetime('now'))", (P,))
    conn.execute("INSERT INTO anomaly_note (invoice_no, dim, period, note, confirmed_at) "
                 "VALUES ('M2','handler',?, '老维度备注', datetime('now'))", (P,))
    conn.commit()
    m = IC.load_confirmations(P, proxy)
    check("A4 load：回退旧维度补空缺（merged/handler）",
          m.get("M1") == "人工备注" and m.get("M2") == "老维度备注"
          and m.get("A1") == "改过的备注", str(m))

    # 同票号两边都有 → 本模块 dim 优先
    conn.execute("INSERT INTO anomaly_note (invoice_no, dim, period, note, confirmed_at) "
                 "VALUES ('M1',?,?, '导入时确认备注', datetime('now'))", (IC.CONFIRM_DIM, P))
    conn.commit()
    check("A5 load：本模块 dim 优先于旧维度（同票号并存）",
          IC.load_confirmations(P, proxy).get("M1") == "导入时确认备注",
          str(IC.load_confirmations(P, proxy)))

    # 本 dim 现有 3 条：A1 / A2 / M1（M1 的 merged 是**另一行**，主键含 dim）
    deleted = IC.clear_confirmations(proxy, P)
    check("A6 clear：只删 import_confirm 的 3 条", deleted == 3, str(deleted))
    left = _dims(P)
    check("A7 clear：旧维度 merged/handler **原样保留**",
          left == {"M1": "merged", "M2": "handler"}, str(left))
    check("A7 clear：本模块留痕已清空（load 只剩回退来源）",
          IC.load_confirmations(P, proxy) == {"M1": "人工备注", "M2": "老维度备注"},
          str(IC.load_confirmations(P, proxy)))

    # ============================================================ B) commit 同事务写
    P2 = "2025-04"
    # 校验 1：sheet1+sheet2 合计须 = 该账期销项合计 → 先塞一张销项票
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('S4', '2025-04-10', '销项甲', 100.0, 'import')")
    conn.commit()
    d = {
        "invoices": [], "deferred": [], "prepayments": [], "problems": [],
        "sheet_totals": {"sheet1": 100.0}, "sheet12_total": 100.0,
        "confirmations": [
            {"invoice_no": "C1", "note": "无经办人"},
            {"invoice_no": "C2", "note": "红字与蓝字原票不一致（总金额）"},
        ],
    }
    r = imp.commit_ledger_import(d, P2, "2025.4台账.xlsx")
    check("B1 commit：返回 confirmation_count",
          r.get("confirmation_count") == 2, str(r.get("confirmation_count")))
    check("B2 commit：留痕落库（票号 → 备注）",
          IC.load_confirmations(P2, proxy) == {
              "C1": "无经办人", "C2": "红字与蓝字原票不一致（总金额）"},
          str(IC.load_confirmations(P2, proxy)))
    check("B3 commit：dim 精确 = import_confirm（不污染 merged）",
          set(_dims(P2).values()) == {IC.CONFIRM_DIM}, str(_dims(P2)))

    # 覆盖式重导同账期 → 上一次的确认对新一批已不适用，必须失效（先清后写）
    d2 = dict(d)
    d2["confirmations"] = [{"invoice_no": "C1", "note": "改过的备注"}]
    r2 = imp.commit_ledger_import(d2, P2, "2025.4台账.xlsx")
    check("B4 覆盖式重导：先清后写（C2 的旧留痕失效）",
          IC.load_confirmations(P2, proxy) == {"C1": "改过的备注"},
          str(IC.load_confirmations(P2, proxy)))
    check("B4 覆盖式重导：confirmation_count 反映本次条数",
          r2.get("confirmation_count") == 1, str(r2.get("confirmation_count")))

    # rollback_batch：本批账期的**三类**留痕一起清，但不动旧页人工备注。
    # ⚠️ 批 3-2b 上线时漏了 `import_edit`（只清了 `import_confirm`）—— 同一族的 dim
    #    必须同生共死：撤销台账批次后，post 页不该再看到「导入时已修改」。本批收口。
    bid = _active_batch(P2)
    IC.save_edit_hints(proxy, P2, [{"invoice_no": "C1", "note": "购方"}])
    IC.save_backfill_hints(proxy, P2, ["C9"])
    conn.execute("INSERT INTO anomaly_note (invoice_no, dim, period, note, confirmed_at) "
                 "VALUES ('KEEP','merged',?, '人工备注', datetime('now'))", (P2,))
    conn.commit()
    # ⚠️ `_dims` 是 {票号: dim} 字典，同票多 dim 会互相顶掉（C1 同时有
    #    import_confirm 与 import_edit）→ 这里必须用 (票号, dim) 二元组。
    _pre2 = {(r["invoice_no"], r["dim"]) for r in conn.execute(
        "SELECT invoice_no, dim FROM anomaly_note WHERE period=?", (P2,))}
    check("B4b 前置：撤销前三类留痕都在（import_confirm / import_edit / import_backfill）",
          {d for _, d in _pre2} == {IC.CONFIRM_DIM, IC.EDIT_DIM, IC.BACKFILL_DIM,
                                    "merged"},
          str(sorted(_pre2)))
    imp.rollback_batch(proxy, bid)
    conn.commit()
    left2 = _dims(P2)
    check("B5 rollback_batch：本批账期的 import_confirm 留痕被清",
          IC.CONFIRM_DIM not in left2.values(), str(left2))
    check("B5 rollback_batch：import_edit / import_backfill 留痕**同样**被清",
          IC.EDIT_DIM not in left2.values() and IC.BACKFILL_DIM not in left2.values(),
          str(left2))
    check("B6 rollback_batch：旧页人工备注 dim='merged' **不动**",
          left2.get("KEEP") == "merged", str(left2))
    check("B6 rollback_batch：批次标记 rolled_back",
          conn.execute("SELECT status FROM import_batch WHERE id=?", (bid,)).fetchone()[0]
          == "rolled_back")

    # ============================================================ C) 读侧带出
    P3 = "2025-05"
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('S5', '2025-05-10', '销项乙', 100.0, 'import')")
    conn.commit()
    d3 = {
        "invoices": [], "deferred": [], "prepayments": [], "problems": [],
        "sheet_totals": {"sheet1": 100.0}, "sheet12_total": 100.0,
        "confirmations": [{"invoice_no": "R1", "note": "无经办人"}],
    }
    imp.commit_ledger_import(d3, P3, "2025.5台账.xlsx")
    rd = RB.rebuild_period_data(P3, proxy)
    check("C1 rebuild：confirmations 随 data 带出（供 post 模式压制）",
          rd.get("confirmations") == {"R1": "无经办人"}, str(rd.get("confirmations")))
    check("C1 rebuild：批次三要素同时带出（同一份 data）",
          rd.get("batch_id") is not None and rd.get("file_name") == "2025.5台账.xlsx",
          f"{rd.get('batch_id')}/{rd.get('file_name')}")

    rd0 = RB.rebuild_period_data("2099-01", proxy)
    check("C2 rebuild：无 active 批次 → confirmations 为空字典（不抛异常）",
          rd0.get("confirmations") == {}, str(rd0.get("confirmations")))
    check("C2 rebuild：空骨架字段齐全（含 confirmations）",
          all(k in rd0 for k in ("invoices", "deferred", "prepayments", "problems",
                                 "sheet_totals", "sheet12_total", "backfills",
                                 "confirmations")))
    check("C2 rebuild：无 active 批次 → backfilled 为空集合（批 3-2c）",
          rd0.get("backfilled") == set(), f"{rd0.get('backfilled')!r}")

    # ============================================================ D) 批 3-2b：修改字段留痕
    P4 = "2025-06"
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('S6', '2025-06-10', '销项丙', 100.0, 'import')")
    conn.commit()
    n = IC.save_edit_hints(proxy, P4, [
        {"invoice_no": "E1", "note": "购方、案号"},
        {"invoice_no": "E2", "note": "经办人分摊"},
        {"invoice_no": "", "note": "空票号应跳过"},
        {"invoice_no": "E3", "note": ""},
    ])
    check("D1 save_edit_hints：写入 2 条（空票号/空字段列表跳过）", n == 2, str(n))
    check("D2 load_edit_hints：票号 → 字段列表",
          IC.load_edit_hints(P4, proxy) == {"E1": "购方、案号", "E2": "经办人分摊"},
          str(IC.load_edit_hints(P4, proxy)))
    check("D3 save_edit_hints：同票幂等（INSERT OR REPLACE）",
          IC.save_edit_hints(proxy, P4, [{"invoice_no": "E1", "note": "购方"}]) == 1
          and IC.load_edit_hints(P4, proxy).get("E1") == "购方")

    # 与确认留痕互不覆盖：同账期两条 dim 并存，clear 各自只清自己。
    # （注意 `_dims` 是 {票号: dim} 字典，同票两 dim 会互相顶掉 —— 这里用 (票号, dim) 二元组。）
    IC.save_confirmations(proxy, P4, [{"invoice_no": "E1", "note": "无经办人"}])
    rows4 = {(r["invoice_no"], r["dim"]) for r in conn.execute(
        "SELECT invoice_no, dim FROM anomaly_note WHERE period=?", (P4,))}
    check("D4 两 dim 并存：互不覆盖",
          {d for _, d in rows4} == {IC.CONFIRM_DIM, IC.EDIT_DIM}
          and ("E1", IC.CONFIRM_DIM) in rows4 and ("E1", IC.EDIT_DIM) in rows4,
          str(sorted(rows4)))
    IC.clear_edit_hints(proxy, P4)
    check("D5 clear_edit_hints：只清 import_edit（确认留痕原样保留）",
          IC.load_edit_hints(P4, proxy) == {}
          and IC.load_confirmations(P4, proxy).get("E1") == "无经办人", str(_dims(P4)))

    # commit 同事务写 + 读侧带出（commit 的「先清本账期」会顺带清掉上面手工造的
    # E1/E2 import_edit 行 —— 同 dim 整体失效，属预期；E1 的 import_confirm 同样被清，
    # 故本段不再回看 D4/D5 的行）。
    d4 = {
        "invoices": [], "deferred": [], "prepayments": [], "problems": [],
        "sheet_totals": {"sheet1": 100.0}, "sheet12_total": 100.0,
        "edit_hints": [{"invoice_no": "F1", "note": "购方、经办人分摊"}],
    }
    r4 = imp.commit_ledger_import(d4, P4, "2025.6台账.xlsx")
    check("D6 commit：返回 edit_hint_count", r4.get("edit_hint_count") == 1,
          str(r4.get("edit_hint_count")))
    check("D7 commit：留痕落库",
          IC.load_edit_hints(P4, proxy) == {"F1": "购方、经办人分摊"},
          str(IC.load_edit_hints(P4, proxy)))
    rd4 = RB.rebuild_period_data(P4, proxy)
    check("D8 rebuild：edit_hints 随 data 带出（供 post 页原因列标注）",
          rd4.get("edit_hints") == {"F1": "购方、经办人分摊"}, str(rd4.get("edit_hints")))

    # 覆盖式重导同账期 → 旧修改提示对新一批已不适用，必须失效（先清后写）
    r4b = imp.commit_ledger_import({**d4, "edit_hints": []}, P4, "2025.6台账.xlsx")
    check("D9 覆盖式重导：先清后写（旧留痕失效）",
          IC.load_edit_hints(P4, proxy) == {}, str(IC.load_edit_hints(P4, proxy)))
    check("D9 覆盖式重导：edit_hint_count = 0",
          r4b.get("edit_hint_count") == 0, str(r4b.get("edit_hint_count")))

    rd0b = RB.rebuild_period_data("2099-02", proxy)
    check("D10 rebuild：无 active 批次 → edit_hints 空字典（骨架字段齐全）",
          rd0b.get("edit_hints") == {} and "edit_hints" in rd0b,
          str(rd0b.get("edit_hints")))

    # ============================================================ E) 批 3-2c：补录票号留痕
    P5 = "2025-07"
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('S7', '2025-07-10', '销项丁', 100.0, 'import')")
    conn.commit()
    n = IC.save_backfill_hints(proxy, P5, ["B1", "", "   ", None, "B2"])
    check("E1 save_backfill_hints：写入 2 条（空/空白/None 跳过）", n == 2, str(n))
    check("E2 load_backfill_hints：返回**票号集合**（不是 dict）",
          IC.load_backfill_hints(P5, proxy) == {"B1", "B2"},
          f"{IC.load_backfill_hints(P5, proxy)!r}")
    check("E3 save_backfill_hints：同票幂等（INSERT OR REPLACE）",
          IC.save_backfill_hints(proxy, P5, ["B1"]) == 1
          and IC.load_backfill_hints(P5, proxy) == {"B1", "B2"}
          and len([1 for r in conn.execute(
              "SELECT 1 FROM anomaly_note WHERE period=? AND invoice_no='B1'", (P5,))]) == 1)
    check("E3 save_backfill_hints：items=None 不炸且返回 0",
          IC.save_backfill_hints(proxy, P5, None) == 0)

    # 三只 dim 并存、互不覆盖；clear 各自只清自己
    IC.save_confirmations(proxy, P5, [{"invoice_no": "B1", "note": "无经办人"}])
    IC.save_edit_hints(proxy, P5, [{"invoice_no": "B1", "note": "购方"}])
    _rows5 = {(r["invoice_no"], r["dim"]) for r in conn.execute(
        "SELECT invoice_no, dim FROM anomaly_note WHERE period=?", (P5,))}
    check("E4 三 dim 并存：同一票号同时有三类留痕，互不覆盖",
          {d for _, d in _rows5} == {IC.CONFIRM_DIM, IC.EDIT_DIM, IC.BACKFILL_DIM}
          and all(("B1", d) in _rows5 for d in (IC.CONFIRM_DIM, IC.EDIT_DIM,
                                                IC.BACKFILL_DIM)),
          str(sorted(_rows5)))
    IC.clear_backfill_hints(proxy, P5)
    check("E5 clear_backfill_hints：只清 import_backfill",
          IC.load_backfill_hints(P5, proxy) == set()
          and IC.load_confirmations(P5, proxy).get("B1") == "无经办人"
          and IC.load_edit_hints(P5, proxy).get("B1") == "购方",
          str(sorted({(r["invoice_no"], r["dim"]) for r in conn.execute(
              "SELECT invoice_no, dim FROM anomaly_note WHERE period=?", (P5,))})))

    # commit 同事务写 + 读侧带出 + 覆盖式重导先清后写
    d5 = {
        "invoices": [], "deferred": [], "prepayments": [], "problems": [],
        "sheet_totals": {"sheet1": 100.0}, "sheet12_total": 100.0,
        "backfilled": ["BF1", "BF2"],
    }
    r5 = imp.commit_ledger_import(d5, P5, "2025.7台账.xlsx")
    check("E6 commit：返回 backfill_hint_count",
          r5.get("backfill_hint_count") == 2, str(r5.get("backfill_hint_count")))
    check("E7 commit：留痕落库（票号集合）",
          IC.load_backfill_hints(P5, proxy) == {"BF1", "BF2"},
          f"{IC.load_backfill_hints(P5, proxy)!r}")
    check("E7 commit：dim 精确 = import_backfill（不污染前两只 dim）",
          all(d == IC.BACKFILL_DIM for d in _dims(P5).values()), str(_dims(P5)))
    rd5 = RB.rebuild_period_data(P5, proxy)
    check("E8 rebuild：backfilled 随 data 带出",
          rd5.get("backfilled") == {"BF1", "BF2"}, f"{rd5.get('backfilled')!r}")

    r5b = imp.commit_ledger_import({**d5, "backfilled": []}, P5, "2025.7台账.xlsx")
    check("E9 覆盖式重导：先清后写（旧留痕失效）",
          IC.load_backfill_hints(P5, proxy) == set(),
          f"{IC.load_backfill_hints(P5, proxy)!r}")
    check("E9 覆盖式重导：backfill_hint_count = 0",
          r5b.get("backfill_hint_count") == 0, str(r5b.get("backfill_hint_count")))

    rd0c = RB.rebuild_period_data("2099-03", proxy)
    check("E10 rebuild：无 active 批次 → backfilled 空集合（骨架字段齐全）",
          rd0c.get("backfilled") == set() and "backfilled" in rd0c,
          f"{rd0c.get('backfilled')!r}")

    # ============================================================ 汇总
    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + " | ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
