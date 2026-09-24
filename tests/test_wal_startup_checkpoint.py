"""启动时 WAL checkpoint（P3-1）单元测试 — 临时库。

场景：崩溃/强杀后 -wal 残留（模拟 = 持有一个已提交但未关闭的连接），
下次启动 init_db() 应把 wal 合入主库并截断（TRUNCATE），否则 Seafile 同步的
只是主库本体，另一台 PC 打开的是未合入 WAL 的旧库。

运行：python tests/test_wal_startup_checkpoint.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.db as dbmod  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def main() -> int:
    old_path = dbmod.DB_PATH
    tmp = tempfile.mkdtemp(prefix="p3_wal_")
    dbmod.DB_PATH = Path(tmp) / "lawfirm.db"
    holder = None
    try:
        # 1) 首次 init_db：建库（表已由 executescript 建，wal 随连接关闭自动消失）
        dbmod.init_db(backfill=False)
        wal = Path(str(dbmod.DB_PATH) + "-wal")
        check("init_db 幂等建库成功", dbmod.DB_PATH.exists())

        # 2) 模拟崩溃残留：持有一个打开的连接并提交数据（不关）→ wal 有内容
        holder = sqlite3.connect(str(dbmod.DB_PATH))
        holder.execute("PRAGMA journal_mode = WAL")
        holder.execute("INSERT INTO staff (name, staff_type, is_active) "
                       "VALUES ('探针', '聘用', 1)")
        holder.commit()
        check("残留 wal 存在且有内容",
              wal.exists() and os.path.getsize(wal) > 0,
              f"exists={wal.exists()} size={os.path.getsize(wal) if wal.exists() else 0}")

        # 3) 再次 init_db（= 下次启动）：尾部 TRUNCATE checkpoint 应清空 wal
        dbmod.init_db(backfill=False)
        size = os.path.getsize(wal) if wal.exists() else 0
        check("启动 checkpoint 后 wal 已截断", size == 0, f"size={size}")

        # 4) 数据仍在（wal 合入主库，不是丢数据）
        c = sqlite3.connect(str(dbmod.DB_PATH))
        n = c.execute("SELECT COUNT(*) FROM staff WHERE name='探针'").fetchone()[0]
        c.close()
        check("wal 内容已合入主库", n == 1, f"rows={n}")
    finally:
        if holder is not None:
            holder.close()
        dbmod.DB_PATH = old_path

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
