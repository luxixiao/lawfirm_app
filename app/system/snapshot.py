"""快照：保存 / 恢复 / 删除（手动 + 导入前自动）"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from app.db import DB_PATH, checkpoint, get_conn

SNAP_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "snapshots"
MAX_AUTO = 20  # 自动快照最多保留数量


def save_snapshot(name: str, note: str = "", auto: bool = False) -> int:
    """保存当前数据为快照（先 checkpoint 保证 db 单一文件），返回快照 id"""
    checkpoint()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_dir = SNAP_ROOT / ts
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "lawfirm.db"
    shutil.copy2(DB_PATH, dest)

    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO snapshot (name, created_at, db_backup, note) VALUES (?,?,?,?)",
            (name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(dest), note),
        )
        snap_id = cur.lastrowid
        # 自动快照数量限制
        if auto:
            rows = conn.execute(
                "SELECT id FROM snapshot WHERE note LIKE '%自动%' ORDER BY id DESC"
            ).fetchall()
            for r in rows[MAX_AUTO:]:
                _delete_snapshot(conn, r["id"])
        conn.commit()
        return snap_id
    finally:
        conn.close()


def _delete_snapshot(conn, snap_id: int) -> None:
    """删除快照记录与文件（须在事务内调用）"""
    row = conn.execute("SELECT db_backup FROM snapshot WHERE id=?", (snap_id,)).fetchone()
    if row:
        p = Path(row["db_backup"])
        if p.exists():
            shutil.rmtree(p.parent, ignore_errors=True)
    conn.execute("DELETE FROM snapshot WHERE id=?", (snap_id,))


def list_snapshots() -> list:
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM snapshot ORDER BY id DESC").fetchall()
    finally:
        conn.close()


def restore_snapshot(snap_id: int) -> str:
    """恢复快照：覆盖当前数据库（返回提示信息）。

    需先 checkpoint 并关闭全部连接；Windows 文件锁可能导致失败，此时提示重启应用。
    """
    conn = get_conn()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        row = conn.execute("SELECT db_backup, name FROM snapshot WHERE id=?", (snap_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(f"快照 {snap_id} 不存在")
        src = Path(row["db_backup"])
        if not src.exists():
            raise FileNotFoundError(f"快照文件缺失: {src}")
        # 关闭所有连接（SQLite 短连接已关闭，此处确保 checkpoint 后无写事务）
        conn.close()
        shutil.copy2(src, DB_PATH)
        for suffix in ("-wal", "-shm"):
            p = Path(str(DB_PATH) + suffix)
            if p.exists():
                p.unlink(missing_ok=True)
        return f"已恢复到快照「{row['name']}」。\n请在导入记录页确认数据，建议重启应用。"
    except PermissionError:
        return "恢复失败：数据库文件被占用。请关闭应用后重新打开，再执行恢复。"
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def delete_snapshot(snap_id: int) -> None:
    conn = get_conn()
    try:
        _delete_snapshot(conn, snap_id)
        conn.commit()
    finally:
        conn.close()
