"""补录页 confirm 后局部刷新回归（offscreen）。

验证 _open_dialog 保存成功后的刷新路径（_apply_backfill_result / _remove_pending_row）：
只把刚入库的那一张从「待补录」移除，不再整表重建 —— 这是修复
「点确定后过 2 秒才更新」的核心改动。

运行：python tests/test_manual_entry_refresh.py
"""
import os
import sys
import sqlite3
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from pathlib import Path  # noqa: E402
import app.db as dbmod  # noqa: E402
from app.db import init_db  # noqa: E402
from app.engine import backfill_module as bm  # noqa: E402
from app.engine import raw_ledger as rl  # noqa: E402
import app.ui.manual_entry_view as MV  # noqa: E402


def _make_conn(tmp):
    c = sqlite3.connect(tmp)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")
    return c


def main() -> int:
    # 临时库（文件库，多连接安全）
    tmp = os.path.join(tempfile.gettempdir(), "lawfirm_me_refresh.db")
    for s in ("", "-wal", "-shm"):
        p = tmp + s
        if os.path.exists(p):
            os.remove(p)
    dbmod.DB_PATH = Path(tmp)
    init_db(backfill=False)

    # 注入：所有 get_conn 指向临时库
    for mod in (dbmod, bm, rl, MV):
        mod.get_conn = lambda: _make_conn(tmp)

    conn = _make_conn(tmp)
    conn.execute("INSERT OR IGNORE INTO staff_roster(name) VALUES ('周立生')")
    conn.execute(
        "INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) "
        "VALUES ('周立生', '聘用', 1)")
    conn.execute(
        "INSERT INTO import_batch (id, batch_type, period, file_name, status, imported_at) "
        "VALUES (1, 'ledger', '2025-06', 'x.xlsx', 'active', datetime('now','localtime'))")
    # 源 A：红字引用缺失原票 → 待补录
    conn.execute(
        "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source, orig_invoice_no) "
        "VALUES ('RED1', '2025-06-20', '丙公司', -300.0, 'import', 'MISS1')")
    conn.execute(
        "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source) "
        "VALUES ('RED1', '周立生', -300.0, 'import')")
    conn.commit()
    conn.close()

    v = MV.ManualEntryView()  # __init__ 会自刷新

    def has_pending(no):
        t = v.tab_pending
        for r in range(t.rowCount()):
            it = t.item(r, 2)  # 第 2 列 = 发票号码
            if it is not None and it.text() == no:
                return True
        return False

    def has_done(no):
        t = v.tab_done
        for r in range(t.rowCount()):
            it = t.item(r, 1)  # 第 1 列 = 发票号码
            if it is not None and it.text() == no:
                return True
        return False

    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}", flush=True)

    check("刷新后 MISS1 在待补录", has_pending("MISS1"))
    check("刷新后 MISS1 不在已补录", not has_done("MISS1"))

    # 模拟 confirm 后写库 + 局部刷新（本次 fix 的路径）
    bm.save_backfill({
        "invoice_no": "MISS1",
        "invoice_date": "2024-05-06",
        "buyer": "丙公司",
        "total_amount": 300.0,
        "handlers": [{"name": "周立生", "billing": 300.0, "received": 0.0, "date": ""}],
    })
    v._apply_backfill_result("MISS1")

    check("局部刷新后 MISS1 从待补录移除", not has_pending("MISS1"))
    check("局部刷新后 MISS1 进入已补录", has_done("MISS1"))

    # 编辑已补录项：再次局部刷新不应触碰待补录（MISS1 本就不在待补录）
    v._apply_backfill_result("MISS1")
    check("编辑刷新后待补录仍不含 MISS1（无残留/无崩溃）", not has_pending("MISS1"))

    # 断言「未整表重建」：若仍调用整表 refresh，待补录会重新从库扫描 —— 这里仅验证
    # 局部移除后表行数守恒（待补录应只剩 0 行，已补录 1 行），与全量重扫结果一致。
    check("待补录清空（仅 1 张源A 时）", v.tab_pending.rowCount() == 0,
          f"rowCount={v.tab_pending.rowCount()}")
    check("已补录 1 行", v.tab_done.rowCount() == 1,
          f"rowCount={v.tab_done.rowCount()}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
