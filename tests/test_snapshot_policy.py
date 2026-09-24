"""快照策略（P2-3）单元测试 — 临时目录 + 临时库。

覆盖：
1. `_auto_snapshot`：无 active 批次（首导）不产生快照；同 type+period 有 active
   批次（真会覆盖）才快照；
2. `save_snapshot` auto 裁剪：auto 行超 20 后裁到 20；
3. `sweep_orphans`：表未引用的孤儿目录被删；被引用的旧同步目录（LEGACY_ROOT）
   迁移到新根并更新 db_backup 指向。

运行：python tests/test_snapshot_policy.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.system import snapshot as snap  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


class _Env:
    """临时库 + 重定向快照根（新旧两个根都在临时目录）。"""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="p2_snap_")
        self.db_path = os.path.join(self.tmp, "lawfirm.db")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(staff)")]
        if "hire_month" not in cols:
            conn.execute("ALTER TABLE staff ADD COLUMN hire_month TEXT DEFAULT ''")
        conn.commit()
        conn.close()
        self.snap_root = Path(self.tmp) / "snaps_new"
        self.legacy_root = Path(self.tmp) / "snaps_old"
        self._old = (snap.DB_PATH, snap.SNAP_ROOT, snap.LEGACY_ROOT,
                     snap.get_conn, snap.checkpoint)
        snap.DB_PATH = self.db_path
        snap.SNAP_ROOT = self.snap_root
        snap.LEGACY_ROOT = self.legacy_root
        snap.checkpoint = lambda: None
        snap.get_conn = lambda: self._conn()

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def close(self):
        snap.DB_PATH, snap.SNAP_ROOT, snap.LEGACY_ROOT, snap.get_conn, snap.checkpoint = self._old


def _dirs(p: Path):
    return sorted(d.name for d in p.iterdir()) if p.exists() else []


def main() -> int:
    # ===== 1. _auto_snapshot 策略 =====
    import app.importer.importer as imp
    env = _Env()
    try:
        old = imp.get_conn
        imp.get_conn = env._conn
        calls = []
        old_save = snap.save_snapshot
        snap.save_snapshot = lambda *a, **k: calls.append((a, k)) or 1
        try:
            imp._auto_snapshot("invoice", "2025-01")
            check("首导（无 active 批次）不快照", calls == [], f"calls={len(calls)}")
            c = env._conn()
            c.execute("INSERT INTO import_batch(batch_type, period, file_name, status, imported_at) "
                      "VALUES('invoice','2025-01','a.xls','active', datetime('now','localtime'))")
            c.commit()
            c.close()
            imp._auto_snapshot("invoice", "2025-01")
            check("同 type+period 有 active 批次才快照", len(calls) == 1, f"calls={len(calls)}")
            imp._auto_snapshot("invoice", "2099-01")
            check("不同账期仍不快照", len(calls) == 1, f"calls={len(calls)}")
        finally:
            snap.save_snapshot = old_save
        imp.get_conn = old
    finally:
        env.close()

    # ===== 2. auto 裁剪到 20 =====
    env = _Env()
    try:
        for i in range(22):
            snap.save_snapshot(f"自动{i}", "自动", auto=True)
        c = env._conn()
        n = c.execute("SELECT COUNT(*) FROM snapshot WHERE note LIKE '%自动%'").fetchone()[0]
        c.close()
        check("auto 快照裁剪到 20", n == 20, f"n={n}")
        check("裁剪连文件一起删（新根目录数=20）", len(_dirs(env.snap_root)) == 20,
              f"dirs={len(_dirs(env.snap_root))}")
    finally:
        env.close()

    # ===== 3. sweep_orphans：删孤儿 + 迁移被引用的旧根目录 =====
    env = _Env()
    try:
        # 被引用：LEGACY_ROOT 下造目录+文件，插行指向它
        ref_dir = env.legacy_root / "20260101_000000_ref"
        ref_dir.mkdir(parents=True)
        (ref_dir / "lawfirm.db").write_bytes(b"x" * 100)
        c = env._conn()
        c.execute("INSERT INTO snapshot(name, created_at, db_backup, note) VALUES(?,?,?,?)",
                  ("保留", "2026-01-01 00:00:00", str(ref_dir / "lawfirm.db"), "手动"))
        # 孤儿：新旧根各一个未被引用的目录
        (env.snap_root / "20260102_000000_orphan_new").mkdir(parents=True)
        (env.legacy_root / "20260103_000000_orphan_old").mkdir(parents=True)
        c.commit()
        c.close()
        removed = snap.sweep_orphans()
        check("sweep 返回删除数=2", removed == 2, f"removed={removed}")
        check("新根孤儿已删", "20260102_000000_orphan_new" not in _dirs(env.snap_root))
        check("旧根孤儿已删", "20260103_000000_orphan_old" not in _dirs(env.legacy_root))
        check("被引用目录已迁移到新根", "20260101_000000_ref" in _dirs(env.snap_root),
              f"dirs={_dirs(env.snap_root)}")
        c = env._conn()
        bp = c.execute("SELECT db_backup FROM snapshot WHERE name='保留'").fetchone()[0]
        c.close()
        check("db_backup 已指向新根", str(env.snap_root) in bp, bp)
        check("迁移后文件存在（restore 可用）", os.path.exists(bp), bp)
    finally:
        env.close()

    # ===== 4. zip 快照 + 恢复 + P3-3 先删后拷 =====
    import shutil as _shutil
    from pathlib import Path as _P

    env = _Env()
    try:
        c = env._conn()
        c.execute("INSERT INTO staff (name, staff_type, is_active) VALUES ('v1','聘用',1)")
        c.commit()
        c.close()
        sid = snap.save_snapshot("zip1", "手动")
        c = env._conn()
        bp = c.execute("SELECT db_backup FROM snapshot WHERE id=?", (sid,)).fetchone()[0]
        c.close()
        check("zip 快照落盘（lawfirm.db.zip）", bp.endswith(".zip") and os.path.exists(bp), bp)
        sdir = _P(bp).parent
        check("目录内只有 zip（无裸 db）",
              [p.name for p in sdir.iterdir()] == ["lawfirm.db.zip"],
              f"{[p.name for p in sdir.iterdir()]}")

        # 修改库 → 从 zip 恢复 → 内容回到 v1
        c = env._conn()
        c.execute("UPDATE staff SET name='v2'")
        c.commit()
        c.close()
        msg = snap.restore_snapshot(sid)
        c = env._conn()
        name = c.execute("SELECT name FROM staff").fetchone()[0]
        c.close()
        check("zip 恢复成功（v2→v1）", name == "v1" and "已恢复" in msg, f"name={name}")

        # 旧目录快照（裸 lawfirm.db）仍可恢复（向后兼容）
        old_dir = env.snap_root / "20250101_000000_old"
        old_dir.mkdir(parents=True)
        _shutil.copy2(env.db_path, old_dir / "lawfirm.db")
        c = env._conn()
        c.execute("INSERT INTO snapshot(name, created_at, db_backup, note) VALUES(?,?,?,?)",
                  ("旧格式", "2025-01-01 00:00:00", str(old_dir / "lawfirm.db"), "手动"))
        old_id = c.execute("SELECT id FROM snapshot WHERE name='旧格式'").fetchone()[0]
        c.execute("UPDATE staff SET name='v3'")
        c.commit()
        c.close()
        msg = snap.restore_snapshot(old_id)
        c = env._conn()
        name = c.execute("SELECT name FROM staff").fetchone()[0]
        c.close()
        check("旧目录快照仍可恢复", name == "v1" and "已恢复" in msg, f"name={name}")

        # P3-3：顺序验证——删 -wal/-shm 必须发生在覆盖主库（copy2）之前。
        # 实测：正常路径 sqlite close 即清 wal/shm（unlink 只是崩溃残留的保险），
        # 故 patch Path.exists 让 -wal/-shm 分支被走到（unlink 记录器真实调用）。
        # 注意：restore 是整库覆盖（snapshot 表也回到存档时点），上一步 legacy
        # 恢复后表已空——重插一行指向现存 zip。
        c = env._conn()
        c.execute("INSERT INTO snapshot(name, created_at, db_backup, note) VALUES(?,?,?,?)",
                  ("zip1b", "2025-01-01 00:00:00", bp, "手动"))
        sid2 = c.execute("SELECT id FROM snapshot WHERE name='zip1b'").fetchone()[0]
        c.commit()
        c.close()
        events: list = []
        orig_unlink = _P.unlink
        orig_exists = _P.exists
        orig_copy2 = snap.shutil.copy2

        def rec_unlink(self, *a, **k):
            events.append("unlink:" + self.name)
            return orig_unlink(self, *a, **k)

        def rec_exists(self):
            if self.name.endswith(("-wal", "-shm")):
                return True
            return orig_exists(self)

        def rec_copy2(src, dst, *a, **k):
            events.append("copy2")
            return orig_copy2(src, dst, *a, **k)

        _P.unlink = rec_unlink
        _P.exists = rec_exists
        snap.shutil.copy2 = rec_copy2
        try:
            msg = snap.restore_snapshot(sid2)
        finally:
            _P.unlink = orig_unlink
            _P.exists = orig_exists
            snap.shutil.copy2 = orig_copy2
        try:
            first_copy = events.index("copy2")
            wal_idx = [i for i, e in enumerate(events[:first_copy]) if e.startswith("unlink:")]
            check("P3-3 删 wal 先于覆盖主库", bool(wal_idx), f"events={events}")
            check("P3-3 恢复成功", "已恢复" in msg, f"msg={msg!r}")
        except ValueError:
            check("P3-3 删 wal 先于覆盖主库", False, f"events={events}")

        # copy2 失败（文件被占用）→ 占用提示；主库未被覆盖（copy2 抛错即未写入）。
        # 注意：上一步 restore 已把 snapshot 表整库覆盖回 zip 存档时点（空表），
        # 须重插一行指向现存 zip。
        c = env._conn()
        c.execute("INSERT INTO snapshot(name, created_at, db_backup, note) VALUES(?,?,?,?)",
                  ("zip1c", "2025-01-01 00:00:00", bp, "手动"))
        sid3 = c.execute("SELECT id FROM snapshot WHERE name='zip1c'").fetchone()[0]
        c.commit()
        c.close()
        snap.shutil.copy2 = lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied"))
        try:
            msg = snap.restore_snapshot(sid3)
        finally:
            snap.shutil.copy2 = orig_copy2
        check("copy2 占用走提示", "被占用" in msg, f"msg={msg!r}")
    finally:
        env.close()

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
