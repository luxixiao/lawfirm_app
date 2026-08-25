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


# 默认「发票相关」过滤哨兵：覆盖发票维度三张表的变更
INVOICE_RELATED = "__invoice_related__"

# 修改记录页「表」下拉选项（value -> 展示 / 过滤）
TABLE_OPTIONS = [
    ("", "全部"),
    (INVOICE_RELATED, "发票相关"),
    ("invoice", "invoice（归一化发票）"),
    ("raw_invoice", "raw_invoice（销项导入镜表）"),
    ("raw_ledger", "raw_ledger（发票台账镜表）"),
    ("charge_detail", "charge_detail（经办人拆分）"),
    ("expense_cat", "expense_cat（费用类型）"),
    ("prepayment", "prepayment（预收款）"),
]


def fetch_log(limit: int = 500, *, table_name: str | None = None,
              record_id: str | None = None, field: str | None = None,
              keyword: str | None = None,
              date_from: str | None = None, date_to: str | None = None) -> list:
    """读取修改记录，支持多维过滤。

    table_name: 具体表名，或 INVOICE_RELATED 哨兵（发票相关三表）。
    record_id:  精确匹配记录 id（右击某行查看其修改历史）。
    field:      字段名精确匹配。
    keyword:    在 record_id/field/old_value/new_value/note 中模糊匹配。
    date_from/to: created_at 区间（本地时间，to 含当天 23:59:59）。
    """
    clauses, params = [], []
    if table_name:
        if table_name == INVOICE_RELATED:
            clauses.append("table_name IN ('invoice','raw_invoice','raw_ledger')")
        else:
            clauses.append("table_name = ?")
            params.append(table_name)
    if record_id:
        clauses.append("record_id = ?")
        params.append(record_id)
    if field:
        clauses.append("field = ?")
        params.append(field)
    if keyword:
        clauses.append("(record_id LIKE ? OR field LIKE ? OR old_value LIKE ? "
                       "OR new_value LIKE ? OR note LIKE ?)")
        params += [f"%{keyword}%"] * 5
    if date_from:
        clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("created_at <= ?")
        params.append(date_to + " 23:59:59")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_conn()
    try:
        return conn.execute(
            f"SELECT * FROM change_log{where} ORDER BY id DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    finally:
        conn.close()
