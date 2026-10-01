"""长驻哑连接（app/db.py）回归：解决 WAL 模式下每次业务连接 close 触发
checkpoint+删 -wal/-shm 文件（本机实测 ~130ms/次）导致的交互卡顿。

机制验证（性能数字会随环境波动，断言从宽；机制断言从严）：
- ensure_keepalive_conn 幂等；
- 有哑连接时，业务连接 connect+close 显著快（<80ms；无哑连接时本机 ~115ms）；
- WAL 读写可见性 / 事务语义不变；
- checkpoint(TRUNCATE) 在哑连接存活时可用（快照/备份依赖）；
- close_keepalive_conn → ensure 可重开。

运行：python tests/test_db_keepalive.py（不依赖 Qt）
"""
import os
import sys
import time
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as dbmod  # noqa: E402
from app.db import init_db  # noqa: E402

# 副本库（绝不碰真库）
tmp = os.path.join(tempfile.gettempdir(), "lawfirm_keepalive.db")
for s in ("", "-wal", "-shm"):
    if os.path.exists(tmp + s):
        os.remove(tmp + s)
dbmod.DB_PATH = __import__("pathlib").Path(tmp)
init_db(backfill=False)


def main() -> int:
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}", flush=True)

    # ---- 1) ensure 幂等 ----
    dbmod.ensure_keepalive_conn()
    k1 = dbmod._keepalive_conn
    dbmod.ensure_keepalive_conn()
    check("ensure_keepalive_conn 幂等（同一连接）", dbmod._keepalive_conn is k1)

    # ---- 2) 有哑连接时业务连接开关显著快 ----
    def open_close():
        c = dbmod.get_conn()
        c.execute("SELECT COUNT(*) FROM staff_roster").fetchone()
        c.close()

    open_close()  # 预热（Defender 首扫等一次性成本）
    t0 = time.perf_counter()
    for _ in range(10):
        open_close()
    avg_ms = (time.perf_counter() - t0) / 10 * 1000
    check("有哑连接时 connect+query+close 平均 < 80ms", avg_ms < 80,
          f"avg={avg_ms:.2f} ms")

    # ---- 3) WAL 读写可见性（事务语义不变）----
    c = dbmod.get_conn()
    c.execute("INSERT OR IGNORE INTO staff_roster(name) VALUES ('__ka_probe__')")
    c.commit()
    c.close()
    c2 = dbmod.get_conn()
    n = c2.execute(
        "SELECT COUNT(*) FROM staff_roster WHERE name='__ka_probe__'").fetchone()[0]
    c2.execute("DELETE FROM staff_roster WHERE name='__ka_probe__'")
    c2.commit()
    c2.close()
    check("跨连接写入可见（WAL 语义不变）", n == 1, f"n={n}")

    # ---- 4) checkpoint(TRUNCATE) 在哑连接存活时可用 ----
    c3 = dbmod.get_conn()
    c3.execute("INSERT OR IGNORE INTO staff_roster(name) VALUES ('__ka_probe2__')")
    c3.commit()
    c3.close()
    c4 = dbmod.get_conn()
    c4.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    wal = tmp + "-wal"
    wal_size = os.path.getsize(wal) if os.path.exists(wal) else 0
    c4.close()
    check("checkpoint(TRUNCATE) 后 wal 已清空", wal_size == 0,
          f"size={wal_size}")
    c5 = dbmod.get_conn()
    n2 = c5.execute(
        "SELECT COUNT(*) FROM staff_roster WHERE name='__ka_probe2__'").fetchone()[0]
    c5.execute("DELETE FROM staff_roster WHERE name='__ka_probe2__'")
    c5.commit()
    c5.close()
    check("checkpoint 后数据仍在（已并入主库）", n2 == 1, f"n={n2}")

    # ---- 5) close_keepalive_conn → ensure 重开 ----
    dbmod.close_keepalive_conn()
    check("close_keepalive_conn 后置空", dbmod._keepalive_conn is None)
    dbmod.ensure_keepalive_conn()
    check("ensure 可重开", dbmod._keepalive_conn is not None)
    dbmod.close_keepalive_conn()  # 清理（子进程退出也无妨）

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
