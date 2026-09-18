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

    # rollback_batch：本批账期留痕被清，但不动旧页人工备注
    bid = _active_batch(P2)
    conn.execute("INSERT INTO anomaly_note (invoice_no, dim, period, note, confirmed_at) "
                 "VALUES ('KEEP','merged',?, '人工备注', datetime('now'))", (P2,))
    conn.commit()
    imp.rollback_batch(proxy, bid)
    conn.commit()
    left2 = _dims(P2)
    check("B5 rollback_batch：本批账期的 import_confirm 留痕被清",
          IC.CONFIRM_DIM not in left2.values(), str(left2))
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

    # ============================================================ 汇总
    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + " | ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
