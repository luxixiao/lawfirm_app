"""单元测试 —— 导入/撤销各类 DELETE 的**作用域**（P0-3 方案 A + 止血 B + G2）回归网。

运行：python tests/test_import_delete_scope.py

背景（业务铁律）：台账收款一律**追加**，绝不删历史。此前多处 `DELETE ... WHERE invoice_no=?`
不带批次限定（unscoped），会把该票**其它账期批次**的 import 收款与 manual 补录一并抹掉。
本文件把「删到什么范围」钉成可执行契约，任何一处改回整票删除都会立刻变红。

覆盖落点：
1. `importer._write_collection_for_invoice`  主导入：只删本批（append 语义）
2. 同上同批重跑：幂等，不翻倍
3. `importer._rewrite_collection_for_invoice` 复核回写：整票重写 import（G6，含他批）
4. 同上超收被拦：既有数据一行不动
5. `raw_invoice.delete_rows` 级联删除：只删本批 import（G2）
6. 同上 `import_batch_id IS NULL` 分支：手动建行也保护 manual 收款
7. `importer.rollback_batch`：只清本批，他批发票与收款不受影响
8. `importer._append_receipts_to_existing`（A10）：追加不删历史 + 同批幂等
9. `collection.over_collection_message` 的 `exclude_batch_id`：只排本批，他批仍计入

不碰真库：内存库用 :memory: + SCHEMA；`delete_rows` 用例因其内部自己 get_conn()+close()
（close 会销毁 :memory:），改用临时文件库并对 `app.engine.raw_invoice.get_conn` 打桩。
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine.collection import over_collection_message  # noqa: E402
import app.engine.raw_invoice as ri  # noqa: E402
from app.importer.importer import (  # noqa: E402
    _append_receipts_to_existing,
    _has_collection_not_in_batch,
    _rewrite_collection_for_invoice,
    _write_collection_for_invoice,
    rollback_batch,
)

OK, FAILS = 0, []

# SCHEMA 之后由代码保证的增量列（与其它测试文件保持一致）
_ALTERS = (
    "ALTER TABLE charge_detail ADD COLUMN person_type TEXT DEFAULT ''",
    "ALTER TABLE charge_detail ADD COLUMN received_override REAL DEFAULT NULL",
    "ALTER TABLE charge_detail ADD COLUMN src_sheet TEXT DEFAULT ''",
    "ALTER TABLE charge_detail ADD COLUMN src_row INTEGER DEFAULT 0",
    "ALTER TABLE collection ADD COLUMN src_sheet TEXT DEFAULT ''",
    "ALTER TABLE collection ADD COLUMN src_row INTEGER DEFAULT 0",
    "ALTER TABLE invoice ADD COLUMN src_sheet TEXT DEFAULT ''",
    "ALTER TABLE invoice ADD COLUMN src_row INTEGER DEFAULT 0",
    "ALTER TABLE staff ADD COLUMN hire_month TEXT DEFAULT ''",
)


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
    _apply_alters(conn)
    conn.commit()
    return conn


def _apply_alters(conn):
    for stmt in _ALTERS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # 列已存在（重复调用）


# --------------------------------------------------------------------------
# 数据构造
# --------------------------------------------------------------------------
def add_invoice(conn, no, total, source="import", batch=None, orig=""):
    conn.execute(
        "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, kind, source, "
        "import_batch_id, orig_invoice_no, created_at) VALUES (?,?,?,?,?,?,?,?, datetime('now','localtime'))",
        (no, "2025-01-01", "甲", total, "", source, batch, orig),
    )


def add_collection(conn, no, amt, source="import", batch=None, date="2025-01-01"):
    conn.execute(
        "INSERT INTO collection (invoice_no, amount, receipt_date, source, import_batch_id) "
        "VALUES (?,?,?,?,?)",
        (no, amt, date, source, batch),
    )


def add_charge(conn, no, name, amt, batch=None, source="import"):
    conn.execute(
        "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source, import_batch_id) "
        "VALUES (?,?,?,?,?)",
        (no, name, amt, source, batch),
    )


def add_raw(conn, no, batch):
    cur = conn.execute(
        "INSERT INTO raw_invoice (sheet_name, row_no, invoice_no, synced, import_batch_id) "
        "VALUES (?,?,?,?,?)",
        ("s", 1, no, 1, batch),
    )
    return cur.lastrowid


def coll_sum(conn, no):
    r = conn.execute("SELECT COALESCE(SUM(amount),0) AS s FROM collection WHERE invoice_no=?",
                     (no,)).fetchone()
    return float(r["s"])


def sum_by(conn, no, source=None, batch=None):
    sql = "SELECT COALESCE(SUM(amount),0) AS s FROM collection WHERE invoice_no=?"
    ps = [no]
    if source is not None:
        sql += " AND source=?"
        ps.append(source)
    if batch is not None:
        sql += " AND import_batch_id=?"
        ps.append(batch)
    return float(conn.execute(sql, ps).fetchone()["s"])


def charge_count(conn, no, batch=None):
    sql = "SELECT COUNT(*) AS c FROM charge_detail WHERE invoice_no=?"
    ps = [no]
    if batch is not None:
        sql += " AND import_batch_id=?"
        ps.append(batch)
    return int(conn.execute(sql, ps).fetchone()["c"])


def _inv_pure(no, total, is_red=False):
    return {"invoice_no": no, "is_red": is_red, "total_amount": total,
            "remark": {"pure_date": "2025-01-15", "receipts": []},
            "sheet_name": "s", "row_no": 1}


def _inv_split(no, total, receipts):
    return {"invoice_no": no, "is_red": False, "total_amount": total,
            "remark": {"pure_date": "", "receipts": receipts},
            "sheet_name": "s", "row_no": 1}


def near(a, b):
    return abs(float(a) - float(b)) < 0.01


# --------------------------------------------------------------------------
# 临时文件库（delete_rows 用）
# --------------------------------------------------------------------------
def make_file_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _apply_alters(conn)
    conn.commit()
    conn.close()
    return path


def _file_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def delete_rows_on(path, ids, note=""):
    """在临时文件库上跑 ri.delete_rows（打桩其模块内 get_conn，避免碰真库）。"""
    orig = ri.get_conn
    ri.get_conn = lambda: _file_conn(path)
    try:
        return ri.delete_rows(ids, note)
    finally:
        ri.get_conn = orig


def _safe_close(conn):
    """用例中途断言失败时也要关掉连接，否则临时文件删不掉（Windows 文件占用）。"""
    try:
        if conn is not None:
            conn.close()
    except Exception:  # noqa: BLE001
        pass


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def main():
    # 1) 主导入只删本批：他批 import 与 manual 是历史，一行不删
    c = build_conn()
    add_invoice(c, "A1", 1000.0, batch=1)
    add_collection(c, "A1", 300.0, batch=1)            # 本批旧行 → 应被替换
    add_collection(c, "A1", 200.0, batch=2)            # 他批历史 → 保留
    add_collection(c, "A1", 100.0, source="manual")     # manual 补录 → 保留
    msg = _write_collection_for_invoice(c, _inv_split("A1", 1000.0, [("2025-01", 400)]), 1)
    check("1) 主导入只删本批（他批/manual 保留）",
          msg is None and near(coll_sum(c, "A1"), 700.0)
          and near(sum_by(c, "A1", batch=2), 200.0)
          and near(sum_by(c, "A1", source="manual"), 100.0),
          f"msg={msg!r} sum={coll_sum(c, 'A1')} b2={sum_by(c, 'A1', batch=2)} "
          f"manual={sum_by(c, 'A1', source='manual')}")
    # 止血守卫（方案 B）：该票存在「不属于本批」的 import 收款 → 应能识别
    check("1b) 止血守卫识别出非本批 import 收款",
          _has_collection_not_in_batch(c, "A1", 1) is True)
    c.close()

    # 2) 同批重跑幂等：先删后插，不翻倍
    c = build_conn()
    add_invoice(c, "A2", 1000.0, batch=1)
    m1 = _write_collection_for_invoice(c, _inv_split("A2", 1000.0, [("2025-01", 600)]), 1)
    m2 = _write_collection_for_invoice(c, _inv_split("A2", 1000.0, [("2025-01", 600)]), 1)
    check("2) 同批重跑幂等（不翻倍）",
          m1 is None and m2 is None and near(coll_sum(c, "A2"), 600.0),
          f"m1={m1!r} m2={m2!r} sum={coll_sum(c, 'A2')}")
    c.close()

    # 3) 复核回写（G6）：整票重写该票 import 收款，**含他批遗留 import 行**，manual 不动
    c = build_conn()
    add_invoice(c, "A3", 1000.0, batch=1)
    add_collection(c, "A3", 300.0, batch=1)
    add_collection(c, "A3", 200.0, batch=2)            # 他批 import → 重写须一并清掉
    add_collection(c, "A3", 100.0, source="manual")     # manual → 保留
    msg = _rewrite_collection_for_invoice(c, _inv_split("A3", 1000.0, [("2025-01", 800)]), 9)
    check("3) 复核回写整票重写（清他批 import、留 manual）",
          msg is None and near(coll_sum(c, "A3"), 900.0)
          and near(sum_by(c, "A3", source="manual"), 100.0),
          f"msg={msg!r} sum={coll_sum(c, 'A3')} manual={sum_by(c, 'A3', source='manual')}")
    c.close()

    # 4) 复核回写超收被拦：既有数据一行不动
    c = build_conn()
    add_invoice(c, "A4", 1000.0, batch=1)
    add_collection(c, "A4", 300.0, batch=2)
    add_collection(c, "A4", 100.0, source="manual")
    msg = _rewrite_collection_for_invoice(c, _inv_pure("A4", 1000.0), 9)
    check("4) 复核回写超收被拦、既有不动",
          msg is not None and near(coll_sum(c, "A4"), 400.0),
          f"msg={msg!r} sum={coll_sum(c, 'A4')}")
    c.close()

    # 5) G2：发票台账删已同步行 → 级联只删本批 import，他批/manual 保留
    p = make_file_db()
    conn = None
    try:
        conn = _file_conn(p)
        rid = add_raw(conn, "R1", 7)
        add_invoice(conn, "R1", 1000.0, batch=7)
        add_collection(conn, "R1", 300.0, batch=7)          # 本批 → 删
        add_collection(conn, "R1", 200.0, batch=8)          # 他批 → 保留
        add_collection(conn, "R1", 100.0, source="manual")   # manual → 保留
        add_charge(conn, "R1", "张三", 500.0, batch=7)       # 本批 → 删
        add_charge(conn, "R1", "李四", 400.0, batch=8)       # 他批 → 保留（人名须不同：UNIQUE(invoice_no, person_name)）
        conn.commit()
        conn.close()

        n = delete_rows_on(p, [rid], "pytest")
        conn = _file_conn(p)
        gone = conn.execute("SELECT 1 FROM invoice WHERE invoice_no='R1'").fetchone() is None
        check("5) G2 级联只删本批（他批/manual 保留）",
              n == 1 and gone and near(coll_sum(conn, "R1"), 300.0)
              and near(sum_by(conn, "R1", batch=8), 200.0)
              and near(sum_by(conn, "R1", source="manual"), 100.0)
              and charge_count(conn, "R1", batch=8) == 1,
              f"n={n} gone={gone} sum={coll_sum(conn, 'R1')} "
              f"cd8={charge_count(conn, 'R1', batch=8)}")
        conn.close()
    finally:
        _safe_close(conn)
        _safe_remove(p)

    # 6) G2 的 NULL 批分支：手动建的 raw 行（无批）也只清 import，manual 保留
    p = make_file_db()
    conn = None
    try:
        conn = _file_conn(p)
        rid = add_raw(conn, "R2", None)
        add_invoice(conn, "R2", 1000.0, batch=None)
        add_collection(conn, "R2", 300.0, source="import", batch=None)
        add_collection(conn, "R2", 150.0, source="manual", batch=None)
        add_charge(conn, "R2", "张三", 500.0, batch=None)
        conn.commit()
        conn.close()

        n = delete_rows_on(p, [rid], "pytest")
        conn = _file_conn(p)
        check("6) G2 NULL 批分支（清无批 import、留 manual）",
              n == 1 and near(coll_sum(conn, "R2"), 150.0)
              and near(sum_by(conn, "R2", source="manual"), 150.0)
              and charge_count(conn, "R2") == 0,
              f"n={n} sum={coll_sum(conn, 'R2')} cd={charge_count(conn, 'R2')}")
        conn.close()
    finally:
        _safe_close(conn)
        _safe_remove(p)

    # 7) rollback_batch：只清本批，他批建的发票与收款不受影响
    c = build_conn()
    add_invoice(c, "RB1", 1000.0, batch=2)              # 他批建的票
    add_collection(c, "RB1", 300.0, batch=5)            # 待撤销批 → 删
    add_collection(c, "RB1", 200.0, batch=6)            # 他批 → 保留
    rollback_batch(c, 5)
    alive = c.execute("SELECT 1 FROM invoice WHERE invoice_no='RB1'").fetchone() is not None
    check("7) rollback_batch 只清本批（他批 invoice/collection 保留）",
          alive and near(coll_sum(c, "RB1"), 200.0),
          f"alive={alive} sum={coll_sum(c, 'RB1')}")
    c.close()

    # 8) A10 追加：不删历史 + 同批重跑幂等
    c = build_conn()
    add_invoice(c, "AP1", 1000.0, batch=1)
    add_collection(c, "AP1", 300.0, batch=1)
    m1 = _append_receipts_to_existing(c, "AP1", [("张三", 200.0, "2025-02-01")], 2, "s", 1)
    m2 = _append_receipts_to_existing(c, "AP1", [("张三", 200.0, "2025-02-01")], 2, "s", 1)
    check("8) A10 追加不删历史 + 同批幂等",
          m1 is None and m2 is None and near(coll_sum(c, "AP1"), 500.0),
          f"m1={m1!r} m2={m2!r} sum={coll_sum(c, 'AP1')}")
    c.close()

    # 9) 守卫 exclude_batch_id：只排本批，他批仍计入累计
    c = build_conn()
    add_invoice(c, "OC2", 1000.0, batch=1)
    add_collection(c, "OC2", 500.0, batch=1)            # 本批 → 排除
    add_collection(c, "OC2", 300.0, batch=2)            # 他批 → 计入
    m1 = over_collection_message(c, "OC2", 300.0, exclude_batch_id=1)   # 300+300=600 ≤1000
    m2 = over_collection_message(c, "OC2", 800.0, exclude_batch_id=1)   # 300+800=1100 >1000
    check("9a) exclude_batch_id 排本批、他批仍计入 → 不误报", m1 is None, f"m1={m1!r}")
    check("9b) 他批计入后真超收 → 正确报错", m2 is not None, f"m2={m2!r}")
    c.close()

    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
