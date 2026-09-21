"""员工类型维护 + 员工删除的引用检查

口径（已与需求方确认）：
- **参与结算由 staff_type_def.is_settle 控制**（内置三类 合伙/聘用/兼职 + 公共/行政
  默认参与），**净额口径由 net_basis 控制**（'开票净额' | '收款净额'）。
- person_settlement 改用 `settle_flags_of(name)` 读取，不再按类型名硬编码：
  合伙=开票净额、聘用/兼职=收款净额、其余（含自定义类型中未开启者）=0。
- 故内置三类为 is_builtin=1：**禁止删除、禁止改名**（改名会断结算口径），说明可改。
- 自定义类型（如"顾问""实习"）默认不参与结算，可在员工类型页自行开启参与并选择净额口径。

员工删除：有业务数据引用（charge_detail/collection/expense_ledger/raw_salary）时
禁止删除——硬删会让结算表查不到身份，业务收入被判为 0。
（「停用」功能已于 2026-09 取消：离职人员仍会发生业务，直接保留在花名册中即可，
 不再需要"停用"这种全局开关；见 db.py 的 is_active 迁移。）
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.db import get_conn

# 内置三类：参与结算计算，锁定删除与改名
BUILTIN_TYPES = ["合伙", "聘用", "兼职"]
# 预置但可删可改名的常见类型
DEFAULT_EXTRA = ["挂靠", "其他"]

# 内置默认参与结算的类型及其净额口径（ensure_defaults 回填依据）
SETTLE_DEFAULTS = {"合伙": "开票净额", "聘用": "收款净额", "兼职": "收款净额",
                  "公共": "收款净额", "行政": "收款净额"}


class StaffTypeError(Exception):
    """员工类型/员工删除的业务错误（提示给 UI）。"""


class StaffInUseError(StaffTypeError):
    """员工存在业务数据引用，禁止删除。"""


# ---------------------------------------------------------------------------
# 连接助手（conn 可注入，供内存库单测）
# ---------------------------------------------------------------------------

def _own_conn(conn=None):
    return conn is None, conn if conn is not None else get_conn()


def _close(own: bool, conn) -> None:
    if own:
        conn.close()


# ---------------------------------------------------------------------------
# 类型维护
# ---------------------------------------------------------------------------

def ensure_defaults(conn=None) -> None:
    """建库/升级后补齐：内置三类 + 公共/行政（默认参与结算）+ 预置的挂靠/其他。"""
    own, conn = _own_conn(conn)
    try:
        # 内置三类：参与结算（合伙按开票净额，聘用/兼职按收款净额）
        for i, name in enumerate(BUILTIN_TYPES):
            conn.execute(
                "INSERT OR IGNORE INTO staff_type_def(name, is_builtin, note, sort_order, is_settle, net_basis)"
                " VALUES(?,1,'',?,1,'收款净额')", (name, i + 1))
        # 公共 / 行政：非人员经办人，按需求也参与结算（净额口径=收款净额）
        base = len(BUILTIN_TYPES)
        for j, name in enumerate(("公共", "行政")):
            conn.execute(
                "INSERT OR IGNORE INTO staff_type_def(name, is_builtin, note, sort_order, is_settle, net_basis)"
                " VALUES(?,1,'',?,1,'收款净额')", (name, base + 1 + j))
        # 预置但可删可改名的常见类型：默认不参与结算（is_settle 走列默认值 0）
        # 公共/行政 占 sort_order 4,5；此处从 6 起，避免与 行政(5) 撞序
        extra_base = base + 3
        for k, name in enumerate(DEFAULT_EXTRA):
            conn.execute(
                "INSERT OR IGNORE INTO staff_type_def(name, is_builtin, note, sort_order)"
                " VALUES(?,0,?,?)", (name, "", extra_base + k))
        # 回填：确认列已存在后修正内置类型的净额口径与公共/行政的参与开关（幂等）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]
        if "is_settle" in cols and "net_basis" in cols:
            conn.execute("UPDATE staff_type_def SET is_settle=1, net_basis='开票净额' WHERE name='合伙'")
            conn.execute("UPDATE staff_type_def SET is_settle=1, net_basis='收款净额' WHERE name IN ('聘用','兼职')")
            conn.execute("UPDATE staff_type_def SET is_settle=1, net_basis='收款净额' WHERE name IN ('公共','行政')")
        conn.commit()
    finally:
        _close(own, conn)


def ensure_types(conn, names: List[str]) -> None:
    """导入职工清单时调用：把未知类型自动入库（is_builtin=0）。"""
    if not names:
        return
    for name in names:
        name = (name or "").strip()
        if not name:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO staff_type_def(name, is_builtin, note) VALUES(?,0,'')",
            (name,))


def list_types(conn=None) -> List[Dict]:
    """类型清单：名称/是否内置/说明/排序/引用人数/是否参与计算。"""
    own, conn = _own_conn(conn)
    try:
        ensure_defaults(conn)
        rows = conn.execute(
            """SELECT t.name AS name, t.is_builtin AS is_builtin, t.note AS note,
                      t.sort_order AS sort_order,
                      (SELECT COUNT(*) FROM staff s WHERE s.staff_type = t.name) AS staff_count
               FROM staff_type_def t
               ORDER BY t.sort_order, t.name""").fetchall()
        return [dict(r) for r in rows]
    finally:
        _close(own, conn)


def get_type(name: str, conn=None) -> Optional[Dict]:
    own, conn = _own_conn(conn)
    try:
        r = conn.execute("SELECT * FROM staff_type_def WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None
    finally:
        _close(own, conn)


def is_settle_participant(name: str, conn=None) -> bool:
    """经办人是否参与结算（规则③，导入与详情保存共用）。

    - 空名 → False；
    - 查 staff_type_def.is_settle，True=参与；类型缺失/未参与 → False。
    """
    n = (name or "").strip()
    if not n:
        return False
    own, c = _own_conn(conn)
    try:
        r = c.execute("SELECT is_settle FROM staff_type_def WHERE name=?", (n,)).fetchone()
        return bool(r["is_settle"]) if r else False
    finally:
        _close(own, c)


def settle_flags_of(name: str, conn=None) -> Tuple[bool, str]:
    """(是否参与结算, 净额口径)。类型缺失/未参与 → (False, '收款净额')。"""
    n = (name or "").strip()
    if not n:
        return (False, "收款净额")
    own, c = _own_conn(conn)
    try:
        r = c.execute(
            "SELECT is_settle, net_basis FROM staff_type_def WHERE name=?", (n,)).fetchone()
        if not r:
            return (False, "收款净额")
        return (bool(r["is_settle"]), r["net_basis"] or "收款净额")
    finally:
        _close(own, c)


def set_settle(name: str, flag: bool, conn=None) -> None:
    """员工类型页开关回调：设置是否参与结算。"""
    own, c = _own_conn(conn)
    try:
        c.execute("UPDATE staff_type_def SET is_settle=? WHERE name=?", (1 if flag else 0, name))
        c.commit()
    finally:
        _close(own, c)


def set_net_basis(name: str, basis: str, conn=None) -> None:
    """员工类型页下拉回调：设置净额口径（'开票净额' | '收款净额'）。"""
    own, c = _own_conn(conn)
    try:
        c.execute("UPDATE staff_type_def SET net_basis=? WHERE name=?", (basis, name))
        c.commit()
    finally:
        _close(own, c)


def is_computable(name: str, conn=None) -> bool:
    """该类型是否参与业务收入计算。

    改为读取 staff_type_def.is_settle（参与结算是可设置的开关，见需求）：
    参与结算的内置三类（合伙/聘用/兼职）+ 公共/行政 返回 True，其余 False。
    """
    return is_settle_participant(name, conn)


def add_type(name: str, note: str = "", conn=None) -> None:
    name = (name or "").strip()
    if not name:
        raise StaffTypeError("类型名不能为空")
    if len(name) > 20:
        raise StaffTypeError("类型名过长（≤20 字符）")
    own, conn = _own_conn(conn)
    try:
        dup = conn.execute("SELECT 1 FROM staff_type_def WHERE name=?", (name,)).fetchone()
        if dup:
            raise StaffTypeError(f"类型已存在：{name}")
        nxt = conn.execute(
            "SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM staff_type_def").fetchone()["n"]
        conn.execute(
            "INSERT INTO staff_type_def(name, is_builtin, note, sort_order) VALUES(?,0,?,?)",
            (name, note.strip(), nxt))
        conn.commit()
    finally:
        _close(own, conn)


def rename_type(old: str, new: str, conn=None) -> None:
    """改名：内置三类禁止；新名重复则报错；同步更新 staff 表的类型。"""
    new = (new or "").strip()
    if not new:
        raise StaffTypeError("类型名不能为空")
    own, conn = _own_conn(conn)
    try:
        row = conn.execute("SELECT is_builtin FROM staff_type_def WHERE name=?", (old,)).fetchone()
        if not row:
            raise StaffTypeError(f"类型不存在：{old}")
        if row["is_builtin"]:
            raise StaffTypeError(f"「{old}」是内置结算类型，禁止改名（改名会断结算口径）")
        dup = conn.execute("SELECT 1 FROM staff_type_def WHERE name=?", (new,)).fetchone()
        if dup:
            raise StaffTypeError(f"类型已存在：{new}")
        conn.execute("UPDATE staff_type_def SET name=? WHERE name=?", (new, old))
        conn.execute("UPDATE staff SET staff_type=? WHERE staff_type=?", (new, old))
        conn.commit()
    finally:
        _close(own, conn)


def set_note(name: str, note: str, conn=None) -> None:
    own, conn = _own_conn(conn)
    try:
        conn.execute("UPDATE staff_type_def SET note=? WHERE name=?", (note.strip(), name))
        conn.commit()
    finally:
        _close(own, conn)


def delete_type(name: str, conn=None) -> None:
    """删除类型：内置三类禁止；有员工在用则禁止（避免员工身份丢失）。"""
    own, conn = _own_conn(conn)
    try:
        row = conn.execute("SELECT is_builtin FROM staff_type_def WHERE name=?", (name,)).fetchone()
        if not row:
            raise StaffTypeError(f"类型不存在：{name}")
        if row["is_builtin"]:
            raise StaffTypeError(f"「{name}」是内置结算类型，禁止删除")
        n = conn.execute("SELECT COUNT(*) AS n FROM staff WHERE staff_type=?", (name,)).fetchone()["n"]
        if n:
            raise StaffTypeError(f"还有 {n} 名员工属于该类型，请先把他们改成别的类型再删除")
        conn.execute("DELETE FROM staff_type_def WHERE name=?", (name,))
        conn.commit()
    finally:
        _close(own, conn)


def move_type(name: str, direction: int, conn=None) -> None:
    """上移(-1)/下移(+1)：重写全部 sort_order，保证顺序稳定。"""
    own, conn = _own_conn(conn)
    try:
        names = [r["name"] for r in conn.execute(
            "SELECT name FROM staff_type_def ORDER BY sort_order, name")]
        if name not in names:
            return
        i = names.index(name)
        j = i + direction
        if j < 0 or j >= len(names):
            return
        names[i], names[j] = names[j], names[i]
        for idx, n in enumerate(names):
            conn.execute("UPDATE staff_type_def SET sort_order=? WHERE name=?", (idx + 1, n))
        conn.commit()
    finally:
        _close(own, conn)


# ---------------------------------------------------------------------------
# 员工删除与引用检查
# ---------------------------------------------------------------------------

# 精确匹配人名的表
_REF_TABLES = {
    "charge_detail": "person_name",
    "expense_ledger": "actual_handler",
    "raw_salary": "staff_name",
}

# collection.person_name 留空表示"未归因收款"（结算时按开票份额分摊给各经办人），
# 故须经发票关联 charge_detail 才能反映该员工的真实引用。
_COLLECTION_SQL = (
    "SELECT COUNT(*) AS n FROM collection c "
    "JOIN charge_detail cd ON cd.invoice_no = c.invoice_no "
    "WHERE cd.person_name = ?")


def staff_reference_count(name: str, conn=None) -> Dict[str, int]:
    """该员工在各业务表中的引用条数（collection 含未归因的分摊收款）。"""
    out = {}
    own, conn = _own_conn(conn)
    try:
        for tbl, col in _REF_TABLES.items():
            out[tbl] = conn.execute(
                f"SELECT COUNT(*) AS n FROM {tbl} WHERE {col}=?", (name,)).fetchone()["n"]
        out["collection"] = conn.execute(_COLLECTION_SQL, (name,)).fetchone()["n"]
        out["total"] = sum(v for k, v in out.items() if k != "total")
        return out
    finally:
        _close(own, conn)


def delete_staff(name: str, conn=None) -> None:
    """删除员工；有业务数据引用则抛 StaffInUseError（应保留在花名册中）。"""
    own, conn = _own_conn(conn)
    try:
        refs = staff_reference_count(name, conn)
        if refs["total"] > 0:
            detail = "、".join(f"{k} {v} 条" for k, v in refs.items()
                              if k != "total" and v > 0)
            raise StaffInUseError(
                f"「{name}」在业务数据中有引用（{detail}）。\n"
                f"直接删除会让结算表查不到其身份、业务收入按 0 计。\n"
                f"请保留该员工在花名册中（离职人员仍会发生历史业务，无需删除）。")
        cur = conn.execute("DELETE FROM staff WHERE name=?", (name,))
        if cur.rowcount == 0:
            raise StaffTypeError(f"员工不存在：{name}")
        conn.commit()
    finally:
        _close(own, conn)
