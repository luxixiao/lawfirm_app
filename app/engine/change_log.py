"""修改记录：任何对导入台账数据的修改都会写入 change_log（含手动备注）"""
from __future__ import annotations

from app.db import get_conn


def log_change(conn, table_name: str, record_id: str, field: str,
               old_value, new_value, note: str = "") -> None:
    """写一条修改记录（在事务内调用，与 UPDATE 一起提交）"""
    conn.execute(
        "INSERT INTO change_log (table_name, record_id, field, old_value, new_value, note) "
        "VALUES (?,?,?,?,?,?)",
        (table_name, record_id, field,
         "" if old_value is None else str(old_value),
         "" if new_value is None else str(new_value),
         note or ""),
    )


def log_changes(conn, table_name: str, record_id: str, changes: list, note: str = "") -> None:
    """写多条修改记录；changes = [(field, old, new), ...]"""
    for field, old, new in changes:
        log_change(conn, table_name, record_id, field, old, new, note)


def fetch_log(limit: int = 500) -> list:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM change_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
