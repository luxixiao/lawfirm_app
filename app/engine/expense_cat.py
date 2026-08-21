"""费用类型维护：全集 + 归类（报酬发放/住房公积金/保险费/汽油费/其他）

- 导入费用台账时校验类型在 expense_cat 中，不在 → 报错
- 年度聘用结算表按归类取数
"""
from __future__ import annotations

from app.db import get_conn

CATEGORIES = ["报酬发放", "住房公积金", "保险费", "汽油费", "其他"]

# 预置默认归类规则（按类型名包含关键词）
_DEFAULT_RULE = [
    ("公积金", "住房公积金"),
    ("社保", "保险费"),
    ("保险", "保险费"),
    ("分成", "报酬发放"),
    ("报酬", "报酬发放"),
    ("汽油", "汽油费"),
    ("刷卡", "汽油费"),
    ("停车", "汽油费"),
    ("油", "汽油费"),
]


def ensure_types(conn, types: list) -> None:
    """确保费用类型在 expense_cat 中；缺失的按默认规则自动归类"""
    for t in types:
        if not t:
            continue
        if conn.execute("SELECT 1 FROM expense_cat WHERE expense_type=?", (t,)).fetchone():
            continue
        cat = "其他"
        for kw, c in _DEFAULT_RULE:
            if kw in t:
                cat = c
                break
        conn.execute("INSERT INTO expense_cat (expense_type, category) VALUES (?,?)", (t, cat))


def check_unknown(conn, types: list) -> list:
    """返回不在 expense_cat 名单中的费用类型（用于导入报错）"""
    known = {r["expense_type"] for r in conn.execute("SELECT expense_type FROM expense_cat")}
    return [t for t in types if t and t not in known]


def get_map() -> dict:
    conn = get_conn()
    try:
        return {r["expense_type"]: r["category"] for r in conn.execute("SELECT * FROM expense_cat")}
    finally:
        conn.close()


def get_by_category(category: str) -> list:
    conn = get_conn()
    try:
        return [r["expense_type"] for r in conn.execute(
            "SELECT expense_type FROM expense_cat WHERE category=?", (category,))]
    finally:
        conn.close()


def set_category(expense_type: str, category: str) -> None:
    conn = get_conn()
    try:
        conn.execute("UPDATE expense_cat SET category=? WHERE expense_type=?", (category, expense_type))
        conn.commit()
    finally:
        conn.close()


def add_type(expense_type: str, category: str = "其他") -> None:
    conn = get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO expense_cat (expense_type, category) VALUES (?,?)",
                     (expense_type, category))
        conn.commit()
    finally:
        conn.close()


def sync_from_ledger() -> None:
    """从费用台账同步全部类型到 expense_cat（预置）"""
    conn = get_conn()
    try:
        types = [r["expense_type"] for r in conn.execute(
            "SELECT DISTINCT expense_type FROM expense_ledger WHERE expense_type IS NOT NULL")]
        ensure_types(conn, types)
        conn.commit()
    finally:
        conn.close()
