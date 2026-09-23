"""单元测试 —— 「一张发票不允许超出收款」超收守卫的落点覆盖（引擎层）。

运行：python tests/test_over_collection_guard.py

覆盖两条此前缺失的主路径（2026-09-23 补上；同日按 P0-3 方案 A 更新口径）：
- `importer._write_collection_for_invoice`（主台账导入 sheet1/2 本期发票）：
  按 import_batch_id **追加**写本批 import 收款 —— 只删本批旧行，他批 import 与 manual
  均属历史、一行不删（P0-3 铁律）；故守卫用 exclude_batch_id=<本批> 只排除「即将重插的
  自己」，他批 import + manual **全部计入**累计，真超收必须拦下（不再静默替换历史）。
  ⚠️ 同账期重导入不会重复计数：旧批行已由 `rollback_batch` 在写库前按批清掉。
- `backfill_module.apply_backfill`（补录手动写 collection）：只读预校验放在所有写操作之前，
  确保"早返"不污染既有数据；新票直接比 incoming≤face，已在库票走 over_collection_message。

不碰真库：全部用 :memory: + SCHEMA。
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine.collection import over_collection_message  # noqa: E402
from app.importer.importer import _write_collection_for_invoice  # noqa: E402
from app.engine.backfill_module import apply_backfill  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


def build_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
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
    conn.commit()
    return conn


def add_invoice(conn, no, total, source="import", orig=""):
    conn.execute(
        "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, kind, source, "
        "orig_invoice_no, created_at) VALUES (?,?,?,?,?,?,?, datetime('now','localtime'))",
        (no, "2024-03-01", "甲", total, "", source, orig),
    )


def add_collection(conn, no, amt, source="import", import_batch_id=None):
    conn.execute(
        "INSERT INTO collection (invoice_no, amount, receipt_date, source, import_batch_id) "
        "VALUES (?,?,?,?,?)",
        (no, amt, "2025-01-01", source, import_batch_id),
    )


def coll_sum(conn, no):
    r = conn.execute("SELECT COALESCE(SUM(amount),0) AS s FROM collection WHERE invoice_no=?",
                     (no,)).fetchone()
    return float(r["s"])


def _inv_pure(no, total, is_red=False):
    return {"invoice_no": no, "is_red": is_red, "total_amount": total,
            "remark": {"pure_date": "2025-01-15", "receipts": []},
            "sheet_name": "s", "row_no": 1}


def _inv_split(no, total, receipts):
    return {"invoice_no": no, "is_red": False, "total_amount": total,
            "remark": {"pure_date": "", "receipts": receipts},
            "sheet_name": "s", "row_no": 1}


def _payload(no, total, collections):
    return {"invoice_no": no, "invoice_date": "2025-01-01", "buyer": "甲",
            "total_amount": total, "charge": [("张三", total, "合伙")],
            "collections": collections}


def main():
    # --- _write_collection_for_invoice（主台账导入）---
    # 1) 正常写：无既有收款，pure_date 全额
    c = build_conn()
    add_invoice(c, "INV1", 1000.0)
    msg = _write_collection_for_invoice(c, _inv_pure("INV1", 1000.0), 1)
    check("1) 普通导入正常写（无既有）", msg is None and coll_sum(c, "INV1") == 1000.0,
          f"msg={msg!r} sum={coll_sum(c, 'INV1')}")
    c.close()

    # 2) 超收被拦（既有 manual 500 + 本次 import 1000）
    c = build_conn()
    add_invoice(c, "INV2", 1000.0)
    add_collection(c, "INV2", 500.0, source="manual")
    msg = _write_collection_for_invoice(c, _inv_pure("INV2", 1000.0), 1)
    check("2) 既有 manual+本次 import 超收被拦且不写", msg is not None and coll_sum(c, "INV2") == 500.0,
          f"msg={msg!r} sum={coll_sum(c, 'INV2')}")
    c.close()

    # 3) 他批 import 属历史：必须计入累计、且一行不删（A 方案按批追加，不再整票替换）
    #    既有他批 500 + 本次 1000 = 1500 > 1000 → 真超收，应拦下并保留历史 500。
    c = build_conn()
    add_invoice(c, "INV3", 1000.0)
    add_collection(c, "INV3", 500.0, source="import", import_batch_id=99)
    msg = _write_collection_for_invoice(c, _inv_pure("INV3", 1000.0), 1)
    check("3) 他批 import 计入且受保护（真超收被拦、历史不删）",
          msg is not None and coll_sum(c, "INV3") == 500.0,
          f"msg={msg!r} sum={coll_sum(c, 'INV3')}")
    c.close()

    # 3b) 同批覆盖：本批既有行「先删后插」，exclude_batch_id 排除自己 → 不误报
    #     （同账期重导入的真实形态：旧批行已由 rollback_batch 清掉，此处等价「同批重跑」）
    c = build_conn()
    add_invoice(c, "INV3B", 1000.0)
    add_collection(c, "INV3B", 500.0, source="import", import_batch_id=1)
    msg = _write_collection_for_invoice(c, _inv_pure("INV3B", 1000.0), 1)
    check("3b) 同批先删后插不误报（exclude_batch_id 排自己）",
          msg is None and coll_sum(c, "INV3B") == 1000.0,
          f"msg={msg!r} sum={coll_sum(c, 'INV3B')}")
    c.close()

    # 4) 分月超收被拦（600+600 > 1000）
    c = build_conn()
    add_invoice(c, "INV4", 1000.0)
    msg = _write_collection_for_invoice(c, _inv_split("INV4", 1000.0, [("2025-01", 600), ("2025-02", 600)]), 1)
    check("4) 备注分月超收被拦且不写", msg is not None and coll_sum(c, "INV4") == 0.0,
          f"msg={msg!r} sum={coll_sum(c, 'INV4')}")
    c.close()

    # 5) 红字不写且不被误拦
    c = build_conn()
    add_invoice(c, "INV5", 1000.0)
    msg = _write_collection_for_invoice(c, _inv_pure("INV5", 1000.0, is_red=True), 1)
    check("5) 红字不写 collection 且不被误拦", msg is None and coll_sum(c, "INV5") == 0.0,
          f"msg={msg!r} sum={coll_sum(c, 'INV5')}")
    c.close()

    # 6) 既有 manual 300 + 本次 import 700（≤1000）→ 正常写且保留 manual
    c = build_conn()
    add_invoice(c, "INV6", 1000.0)
    add_collection(c, "INV6", 300.0, source="manual")
    msg = _write_collection_for_invoice(c, _inv_split("INV6", 1000.0, [("2025-01", 700)]), 1)
    check("6) manual 既有+本次 import 不超收正常写", msg is None and coll_sum(c, "INV6") == 1000.0,
          f"msg={msg!r} sum={coll_sum(c, 'INV6')}")
    c.close()

    # --- apply_backfill（补录手动写 collection）---
    # 7) 新票超收被拦（face 1000 < 补录 1200）
    c = build_conn()
    msg = apply_backfill(c, _payload("BF1", 1000.0, [("张三", 1200.0, "2025-01-01")]))
    exist = c.execute("SELECT 1 FROM invoice WHERE invoice_no=?", ("BF1",)).fetchone()
    check("7) 补录新票超收被拦且不建票", msg is not None and exist is None and coll_sum(c, "BF1") == 0.0,
          f"msg={msg!r} exist={exist} sum={coll_sum(c, 'BF1')}")
    c.close()

    # 8) 新票不超收正常写
    c = build_conn()
    msg = apply_backfill(c, _payload("BF2", 1000.0, [("张三", 800.0, "2025-01-01")]))
    row = c.execute("SELECT source, total_amount FROM invoice WHERE invoice_no=?", ("BF2",)).fetchone()
    check("8) 补录新票不超收正常写", msg is None and row is not None and row["source"] == "manual"
          and coll_sum(c, "BF2") == 800.0, f"msg={msg!r} row={row} sum={coll_sum(c, 'BF2')}")
    c.close()

    # 9) 已在库(manual)票 + 既有 import 600 + 补录 manual 500 → 超收被拦（不污染既有）
    c = build_conn()
    add_invoice(c, "BF3", 1000.0, source="manual")
    add_collection(c, "BF3", 600.0, source="import", import_batch_id=5)
    msg = apply_backfill(c, _payload("BF3", 1000.0, [("张三", 500.0, "2025-01-01")]))
    check("9) 补录已在库票超收被拦且不动既有 import", msg is not None and coll_sum(c, "BF3") == 600.0,
          f"msg={msg!r} sum={coll_sum(c, 'BF3')}")
    c.close()

    # 10) 已在库(manual)票 + 既有 import 600 + 补录 manual 300 → 正常（900≤1000）
    c = build_conn()
    add_invoice(c, "BF4", 1000.0, source="manual")
    add_collection(c, "BF4", 600.0, source="import", import_batch_id=5)
    msg = apply_backfill(c, _payload("BF4", 1000.0, [("张三", 300.0, "2025-01-01")]))
    check("10) 补录已在库票不超收正常写（import 保留+manual 新增）",
          msg is None and coll_sum(c, "BF4") == 900.0, f"msg={msg!r} sum={coll_sum(c, 'BF4')}")
    c.close()

    # --- over_collection_message 的 exclude_sources 直测 ---
    c = build_conn()
    add_invoice(c, "OC1", 1000.0)
    add_collection(c, "OC1", 500.0, source="import", import_batch_id=1)
    add_collection(c, "OC1", 300.0, source="manual")
    m1 = over_collection_message(c, "OC1", 100.0, exclude_sources=("import",))
    m2 = over_collection_message(c, "OC1", 800.0)  # 无 exclude：500+300+800 > 1000
    check("11) exclude_sources 只算 manual 既有 → 不误报", m1 is None, f"m1={m1!r}")
    check("12) 无 exclude 全来源 → 正确报超收", m2 is not None, f"m2={m2!r}")
    c.close()

    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
    # 门禁（run_tests.py）按退出码判定成败：不返回非 0 的话失败会被静默放过。
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
