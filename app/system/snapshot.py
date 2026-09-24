"""快照：保存 / 恢复 / 删除（手动 + 导入前自动）

P2 增强：快照以 zip 存储（`lawfirm.db.zip`，DEFLATED 压缩）——SQLite 库文件
压缩率高，可省约 60~70% 磁盘。restore 兼容两种格式：zip（新）与
`lawfirm.db` 裸文件目录（旧快照，恢复路径不变）。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from app.db import DB_PATH, checkpoint, get_conn


def _snap_root() -> Path:
    """P2-3：快照根迁到 %LOCALAPPDATA%/lawfirm_app/snapshots（本机防呆用途，
    无跨机价值，不再进 Seafile 同步目录制造同步风暴）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "lawfirm_app" / "snapshots"


SNAP_ROOT = _snap_root()
# 旧位置（项目 data/ 内，Seafile 同步目录）——sweep_orphans 时迁移/清理
LEGACY_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "snapshots"
MAX_AUTO = 20  # 自动快照最多保留数量


def save_snapshot(name: str, note: str = "", auto: bool = False) -> int:
    """保存当前数据为快照（先 checkpoint 保证 db 单一文件），返回快照 id"""
    checkpoint()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")  # 毫秒级，避免目录撞名
    dest_dir = SNAP_ROOT / ts
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "lawfirm.db.zip"
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(DB_PATH, "lawfirm.db")

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
        # P3-3：先删 -wal/-shm 再覆盖主库。此刻 wal 已被 TRUNCATE 为空文件（内容
        # 已并入主库），先删无副作用；若文件被占用（PermissionError）→ 主库尚未
        # 被覆盖，不会留下「新主库 + 旧 wal」的半程状态，直接走占用提示。
        for suffix in ("-wal", "-shm"):
            p = Path(str(DB_PATH) + suffix)
            if p.exists():
                p.unlink(missing_ok=True)
        if src.suffix == ".zip":
            # P2 增强：zip 快照 → 解到临时目录再覆盖（旧目录快照走原路径）
            with tempfile.TemporaryDirectory(prefix="snap_restore_") as td:
                with zipfile.ZipFile(src) as zf:
                    zf.extractall(td)
                shutil.copy2(Path(td) / "lawfirm.db", DB_PATH)
        else:
            shutil.copy2(src, DB_PATH)
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


def sweep_orphans() -> int:
    """P2-3：清理「snapshot 表未引用」的快照目录（表/盘失同步的孤儿）。

    - 新旧两个根都扫：新根（LOCALAPPDATA）中未引用目录直接删；旧根（data/snapshots，
      Seafile 同步目录）中未引用目录直接删；
    - 旧根中**被引用**的目录迁移到新根，并 UPDATE snapshot.db_backup 指向新位置
      （restore_snapshot 仍可用）；
    - 返回删除的目录数。任一目录删除/移动失败只跳过不抛错（下次启动再试）。
    """
    try:
        conn = get_conn()
        try:
            rows = conn.execute("SELECT id, db_backup FROM snapshot").fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 清扫失败绝不影响启动/导入
        return 0
    referenced: set = set()
    for r in rows:
        p = (r["db_backup"] or "").strip()
        if p:
            referenced.add(Path(p).parent.name)

    removed = 0
    for root in (SNAP_ROOT, LEGACY_ROOT):
        try:
            if not root.is_dir():
                continue
            for d in list(root.iterdir()):
                if not d.is_dir() or d.name in referenced:
                    continue
                shutil.rmtree(d, ignore_errors=True)
                if not d.exists():
                    removed += 1
        except Exception:  # noqa: BLE001
            continue

    # 迁移旧根中被引用的目录 → 新根，并更新 db_backup
    try:
        moved: list = []
        if LEGACY_ROOT.is_dir():
            for d in list(LEGACY_ROOT.iterdir()):
                if not d.is_dir() or d.name not in referenced:
                    continue
                dest = SNAP_ROOT / d.name
                if dest.exists():
                    continue
                try:
                    SNAP_ROOT.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(d), str(dest))
                    moved.append((d, dest))
                except Exception:  # noqa: BLE001 - 文件被占用等，留原位下次再试
                    continue
        if moved:
            conn = get_conn()
            try:
                for src, dest in moved:
                    old_prefix = str(src)
                    for r in rows:
                        bp = (r["db_backup"] or "")
                        if bp.startswith(old_prefix):
                            conn.execute("UPDATE snapshot SET db_backup=? WHERE id=?",
                                         (str(dest / Path(bp).name), r["id"]))
                conn.commit()
            finally:
                conn.close()
    except Exception:  # noqa: BLE001
        pass
    return removed
