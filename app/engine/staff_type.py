"""员工类型维护 + 员工删除的引用检查

口径（已与需求方确认）：
- **员工类型表初始为空**：建库/首次打开不再预置任何类型（合伙/聘用/… 一律由用户在
  「员工类型」页自己新建，见 `ensure_defaults`）。`ensure_types()` 在导入职工清单时把
  清单里出现的未知类型**按需**补入，因此导入是唯一会自动建类型的路径。
- **两条线模型（2026-09-29 重构）**：类型一级只有两个独立开关 ——
  `is_invoice`（是否开票 / 业务线，只管结算收入，不碰费用承担）与
  `can_expense`（是否报销 / 费用线，唯一费用承担闸门）。
  **进报表 = `is_invoice OR can_expense`**（派生，不新增独立开关，代码不写死类型名，D2）。
  净额口径 `net_basis` ∈ {'开票净额','收款净额'}，**仅 `is_invoice=1` 时有意义**，否则空。
- 结算/收入/费用口径一律按「类型开关」（`is_invoice` / `can_expense` / `net_basis`）解析，
  **不引入角色码 / 角色表**（批 C 已彻底移除 `role_def` 表与 `staff_type_def.role_code` 列）。
  改名/删除类型都不会断结算口径；唯一护栏是「有员工在用则禁止删除」（防止员工身份丢失
  →落入 other→收入判 0）。
- person_settlement 改用 `business_flags_of(_person)` 读取业务线口径；`can_bear_expense` 读取
  费用线口径（均按「人员全部类型 OR」解析，D1）。
  类型仍可各自设置 is_invoice / can_expense 与 net_basis（覆盖角色默认值）。

员工删除：有业务数据引用（charge_detail/collection/expense_ledger/raw_salary）时
禁止删除——硬删会让结算表查不到身份，业务收入被判为 0。
（「停用」功能已于 2026-09 取消：离职人员仍会发生业务，直接保留在花名册中即可，
 不再需要"停用"这种全局开关；见 db.py 的 is_active 迁移。）
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.db import get_conn

# 内置三类：参与结算计算，锁定删除与改名
# 注意：这**只是**「禁止删除/改名的内置名单」，不表示建库时会自动建行 ——
# 员工类型表初始为空，这三类与其余类型一样由用户在员工类型页自行新建。
# （expense_cat.py 通过本常量做费用类型的人员白/黑名单判断，勿删。）
BUILTIN_TYPES = ["合伙", "聘用", "兼职"]

# 身份口径（批 C 去写死）：role_def 表与 staff_type_def.role_code 列已彻底移除；
# 结算/收入/费用口径一律按「类型开关」(is_invoice / can_expense / net_basis) 解析，无角色码概念。


def active_types(conn=None):
    """进业务/费用线的类型列表 [(类型名, 类型名)]（去身份：报表分组键=类型名本身）。

    「任一类型勾了开票或报销」即进报表；返回值同时充当分组键与显示名。
    按类型设置页的 sort_order、名称排序。
    """
    own, c = _own_conn(conn)
    try:
        rows = c.execute(
            "SELECT name FROM staff_type_def "
            "WHERE is_invoice = 1 OR can_expense = 1 "
            "ORDER BY sort_order, name").fetchall()
        return [(r["name"], r["name"]) for r in rows]
    finally:
        _close(own, c)


def person_type_combo_items(conn=None):
    """数据行身份下拉项（显示名, 存储值），含「未标」(空值)。供各 UI 编辑/筛选下拉共用。

    去身份：person_type 存**类型名快照**（打标时经办人主类型），下拉列全部类型。
    """
    items = [("未标", "")]
    own, c = _own_conn(conn)
    try:
        rows = c.execute("SELECT name FROM staff_type_def ORDER BY sort_order, name").fetchall()
    finally:
        _close(own, c)
    for r in rows:
        items.append((r["name"], r["name"]))
    return items


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
    """建库/升级后补齐 staff_type_def 的两线列（**只补列，绝不预置任何类型**）。

    行为边界（两条口径，勿再扩大）：
    - **不写行**：员工类型表初始为空，类型一律由用户在员工类型页自行新建；
      导入职工清单时的按需建类型走 `ensure_types()`。
    - **不回填、不回写**：仅做 schema 幂等补列（老库/内存库缺 is_invoice/can_expense/
      net_basis 时补上，存量行落列默认值）。is_settle 旧列已由 init_db 迁移块拆分后
      删除，这里不再新增它。老库的两线值由 `db.init_db` 的迁移块一次性回填，避免
      每次打开都重算（杜绝「勾选取消不掉」式回退）。
    """
    own, conn = _own_conn(conn)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]
        for col, ddl in (("is_invoice", "INTEGER NOT NULL DEFAULT 0"),
                         ("can_expense", "INTEGER NOT NULL DEFAULT 0"),
                         ("net_basis", "TEXT NOT NULL DEFAULT '收款净额'")):
            if col not in cols:
                conn.execute(f"ALTER TABLE staff_type_def ADD COLUMN {col} {ddl}")
        conn.commit()
    finally:
        _close(own, conn)


def ensure_types(conn, names: List[str]) -> None:
    """导入职工清单时调用：把未知类型自动入库（is_builtin=0, net_basis 留空）。

    口径：**初始为空、按需创建**。这里只补「本次导入清单里出现、表里还没有」的类型，
    不预置任何常见类型、也不改动已有类型的 is_invoice / can_expense / net_basis
    （用户勾选为准）。net_basis 默认空：未开票则口径空（设计点2）。
    """
    if not names:
        return
    for name in names:
        name = (name or "").strip()
        if not name:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO staff_type_def(name, is_builtin, note, net_basis) "
            "VALUES(?,0,'','')", (name,))


def list_types(conn=None) -> List[Dict]:
    """类型清单：名称/是否内置/说明/排序/引用人数/是否开票/是否报销/业务金额方式/角色。

    表初始为空 → 首次打开返回 []，用户新建后才有行。
    返回行里的 net_basis **原样透出**（未开票时为 ''），不做兜底改写，供员工类型页直接显示。
    进报表 = is_invoice OR can_expense（派生，未单独存列）。
    """
    own, conn = _own_conn(conn)
    try:
        ensure_defaults(conn)
        rows = conn.execute(
            """SELECT t.name AS name, t.is_builtin AS is_builtin, t.note AS note,
                      t.sort_order AS sort_order, t.is_invoice AS is_invoice,
                      t.can_expense AS can_expense, t.net_basis AS net_basis,
                      (SELECT COUNT(*) FROM staff_type_map m WHERE m.type_name = t.name) AS staff_count
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


def can_bear_expense(name: str, conn=None) -> bool:
    """经办人是否能承担费用（费用线闸门，规则③，导入与详情保存共用）。

    - 空名 → False；
    - 若 name 直接命中 staff_type_def（类型名调用路径）→ 取该类型 can_expense；
    - 否则按经办人姓名解析其**全部类型**（staff_type_map）→ 任一类型 can_expense=1 即 True
      （D1：一人多类型聚合取 OR）；
    - 类型缺失 / 无任何类型可承担 → False。
    （修复：详情保存/导入传入的是「经办人姓名」，旧实现误把姓名当类型名查 staff_type_def，
      导致方国兴=聘用这类理应可承担费用的经办人被判为「未参与」。）
    """
    n = (name or "").strip()
    if not n:
        return False
    own, c = _own_conn(conn)
    try:
        # 1) 类型名直接命中（staff_type_def.name）
        r = c.execute("SELECT can_expense FROM staff_type_def WHERE name=?", (n,)).fetchone()
        if r is not None:
            return bool(r["can_expense"])
        # 2) 经办人姓名 → 解析其全部类型 → OR（D1）
        for t in list_person_types(n, c):
            rr = c.execute("SELECT can_expense FROM staff_type_def WHERE name=?",
                           (t,)).fetchone()
            if rr and rr["can_expense"]:
                return True
        return False
    finally:
        _close(own, c)


def business_flags_of(name: str, conn=None) -> Tuple[bool, str]:
    """单类型的业务线读数：(是否开票, 净额口径)。

    - 名空 / 类型不存在 → (False, "")：无从依据的口径一律返回空，**不做臆造**；
    - 未开票 → (False, 库存的 net_basis 原样)：**不做 `or '收款净额'` 兜底**。
      否则「取消勾选开票时清空 net_basis」存下的空值会被悄悄还原成"收款净额"，
      用户以为清掉了其实还在，重新勾选就会沿用旧口径。
    - 开票 → (True, net_basis or '收款净额')：既然开票就必须有个口径。

    结算侧（person_settlement）只在 is_invoice=True 时读第 2 项，故收窄兜底不影响金额。
    """
    n = (name or "").strip()
    if not n:
        return (False, "")
    own, c = _own_conn(conn)
    try:
        r = c.execute(
            "SELECT is_invoice, net_basis FROM staff_type_def WHERE name=?", (n,)).fetchone()
        if not r:
            return (False, "")
        if not r["is_invoice"]:
            return (False, r["net_basis"] or "")
        return (True, r["net_basis"] or "收款净额")
    finally:
        _close(own, c)


def business_flags_of_person(name: str, conn=None) -> Tuple[bool, str]:
    """**人员级**业务线读数（汇总视图用，落实 D1 一人多类型取 OR）。

    - 返回 (是否任一类型开票, 净额口径)；
    - 净额口径取「主类型优先」：按 (主类型, sort_order) 顺序，取**第一个开票类型**的
      net_basis（主类型自身开票则用它；主类型未开票但次要类型开票，则用该次要类型的口径）。
      这样「主类型优先」且不会出现「主类型未开票却按主类型空口径判 0」的错位。
    - 无任何类型 / 全未开票 → (False, "")。
    """
    n = (name or "").strip()
    if not n:
        return (False, "")
    own, c = _own_conn(conn)
    try:
        types = list_person_types(n, c)   # 主类型在前
        if not types:
            return (False, "")
        for t in types:
            r = c.execute("SELECT is_invoice, net_basis FROM staff_type_def WHERE name=?",
                          (t,)).fetchone()
            if not r:
                continue
            if r["is_invoice"]:
                return (True, r["net_basis"] or "收款净额")
        return (False, "")
    finally:
        _close(own, c)


def set_invoice(name: str, flag: bool, conn=None) -> None:
    """员工类型页「开票」开关回调（业务线）。

    只写 is_invoice，**不碰 net_basis**：净额口径是用户自己选的配置，取消勾选
    只表达「这次不进业务线」，不该顺手删掉。库里留着原口径、页面显示留空，
    两者不冲突：is_invoice=0 时结算侧根本不读 net_basis，金额完全不受影响。
    """
    own, c = _own_conn(conn)
    try:
        c.execute("UPDATE staff_type_def SET is_invoice=? WHERE name=?",
                  (1 if flag else 0, name))
        c.commit()
    finally:
        _close(own, c)


def set_can_expense(name: str, flag: bool, conn=None) -> None:
    """员工类型页「报销」开关回调（费用线）。只写 can_expense。"""
    own, c = _own_conn(conn)
    try:
        c.execute("UPDATE staff_type_def SET can_expense=? WHERE name=?",
                  (1 if flag else 0, name))
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
    """该类型是否进业务线（开票）。读取 staff_type_def.is_invoice。

    该类型勾选开票则返回 True，未勾选/类型不存在返回 False。仅用于单类型名判定；
    人员级「是否产生业务收入」请直接用 person_settlement 的汇总口径（已按多类型 OR）。
    """
    return business_flags_of(name, conn)[0]


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
            "INSERT INTO staff_type_def(name, is_builtin, note, sort_order, net_basis) "
            "VALUES(?,0,?,?, '')", (name, note.strip(), nxt))
        conn.commit()
    finally:
        _close(own, conn)


def rename_type(old: str, new: str, conn=None) -> None:
    """改名：新名重复则报错；同步更新 staff_type_map 的类型名。

    口径按「类型开关」解析（不写死类型名），改名不会再断结算口径，故**放开改名锁**
    （含原「内置三类禁止改名」）。
    """
    new = (new or "").strip()
    if not new:
        raise StaffTypeError("类型名不能为空")
    own, conn = _own_conn(conn)
    try:
        row = conn.execute("SELECT 1 FROM staff_type_def WHERE name=?", (old,)).fetchone()
        if not row:
            raise StaffTypeError(f"类型不存在：{old}")
        dup = conn.execute("SELECT 1 FROM staff_type_def WHERE name=?", (new,)).fetchone()
        if dup:
            raise StaffTypeError(f"类型已存在：{new}")
        conn.execute("UPDATE staff_type_def SET name=? WHERE name=?", (new, old))
        conn.execute("UPDATE staff_type_map SET type_name=? WHERE type_name=?", (new, old))
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
    """删除类型：有员工在用则禁止（避免员工身份丢失）；其余自由删除。

    口径按「类型开关」解析、不写死类型名，故**放开原「内置三类禁止删除」锁**；
    仅保留「有员工引用则禁删」这一真正的安全护栏（防止员工身份被改成空→结算落 other→收入 0）。
    """
    own, conn = _own_conn(conn)
    try:
        row = conn.execute("SELECT 1 FROM staff_type_def WHERE name=?", (name,)).fetchone()
        if not row:
            raise StaffTypeError(f"类型不存在：{name}")
        n = conn.execute("SELECT COUNT(*) AS n FROM staff_type_map WHERE type_name=?", (name,)).fetchone()["n"]
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
        rp = conn.execute("SELECT 1 FROM staff_roster WHERE name=?", (name,)).fetchone()
        conn.execute("DELETE FROM staff_type_map WHERE name=?", (name,))
        conn.execute("DELETE FROM staff_roster WHERE name=?", (name,))
        if not rp:
            raise StaffTypeError(f"员工不存在：{name}")
        conn.commit()
    finally:
        _close(own, conn)


# ---------------------------------------------------------------------------
# 花名册（staff_roster）+ 人员类型关联（staff_type_map）
# 批2：staff 镜像表已彻底移除，人员身份统一来自 staff_roster + staff_type_map。
# ---------------------------------------------------------------------------

def list_roster(conn=None) -> List[Dict]:
    """花名册全量（按姓名序）。列：name/code/id_card/phone/hire_month/leave_month/note。"""
    own, conn = _own_conn(conn)
    try:
        rows = conn.execute(
            "SELECT name, code, id_card, phone, hire_month, leave_month, note "
            "FROM staff_roster ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        _close(own, conn)


def get_roster_person(name: str, conn=None) -> Optional[Dict]:
    own, conn = _own_conn(conn)
    try:
        r = conn.execute("SELECT * FROM staff_roster WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None
    finally:
        _close(own, conn)


def roster_person_exists(name: str, conn=None) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    own, conn = _own_conn(conn)
    try:
        return conn.execute("SELECT 1 FROM staff_roster WHERE name=?", (n,)).fetchone() is not None
    finally:
        _close(own, conn)


def add_roster_person(code: str, name: str, id_card: str, phone: str,
                      hire_month: str, leave_month: str, note: str, conn=None) -> None:
    """新增花名册人员（编号/身份证号非空时必须唯一，由部分唯一索引保证）。"""
    nm = (name or "").strip()
    if not nm:
        raise StaffTypeError("姓名不能为空")
    own, conn = _own_conn(conn)
    try:
        conn.execute(
            "INSERT INTO staff_roster(name, code, id_card, phone, hire_month, leave_month, note) "
            "VALUES(?,?,?,?,?,?,?)",
            (nm, (code or "").strip(), (id_card or "").strip(), (phone or "").strip(),
             (hire_month or "").strip(), (leave_month or "").strip(), (note or "").strip()))
        conn.commit()
    finally:
        _close(own, conn)


def update_roster_person(code: str, name: str, id_card: str, phone: str,
                         hire_month: str, leave_month: str, note: str, conn=None) -> None:
    """修改花名册人员基本信息（编号/身份证号非空时必须唯一）。"""
    nm = (name or "").strip()
    if not nm:
        raise StaffTypeError("姓名不能为空")
    own, conn = _own_conn(conn)
    try:
        conn.execute(
            "UPDATE staff_roster SET code=?, id_card=?, phone=?, hire_month=?, leave_month=?, note=? "
            "WHERE name=?",
            ((code or "").strip(), (id_card or "").strip(), (phone or "").strip(),
             (hire_month or "").strip(), (leave_month or "").strip(), (note or "").strip(), nm))
        # 同步 staff 镜像的入职月份/备注
        conn.commit()
    finally:
        _close(own, conn)


def delete_roster_person(name: str, conn=None) -> None:
    """删除花名册人员；有业务数据引用则抛 StaffInUseError。级联清关联表与 staff 镜像。"""
    nm = (name or "").strip()
    if not nm:
        raise StaffTypeError("姓名不能为空")
    own, conn = _own_conn(conn)
    try:
        refs = staff_reference_count(nm, conn)
        if refs["total"] > 0:
            detail = "、".join(f"{k} {v} 条" for k, v in refs.items()
                              if k != "total" and v > 0)
            raise StaffInUseError(
                f"「{nm}」在业务数据中有引用（{detail}）。\n"
                f"直接删除会让结算表查不到其身份、业务收入按 0 计。\n"
                f"请保留该员工在花名册中（离职人员仍会发生历史业务，无需删除）。")
        conn.execute("DELETE FROM staff_type_map WHERE name=?", (nm,))
        conn.execute("DELETE FROM staff_roster WHERE name=?", (nm,))
        conn.commit()
    finally:
        _close(own, conn)


def list_person_types(name: str, conn=None) -> List[str]:
    """该人的类型名列表（主类型在前，其次按 sort_order、类型名）。"""
    nm = (name or "").strip()
    if not nm:
        return []
    own, conn = _own_conn(conn)
    try:
        rows = conn.execute(
            "SELECT type_name FROM staff_type_map WHERE name=? "
            "ORDER BY is_primary DESC, sort_order, type_name", (nm,)).fetchall()
        return [r["type_name"] for r in rows]
    finally:
        _close(own, conn)


def primary_type_of(name: str, conn=None) -> str:
    """该人主类型（结算用）。无则空串。"""
    nm = (name or "").strip()
    if not nm:
        return ""
    own, conn = _own_conn(conn)
    try:
        r = conn.execute(
            "SELECT type_name FROM staff_type_map WHERE name=? AND is_primary=1", (nm,)).fetchone()
        return r["type_name"] if r else ""
    finally:
        _close(own, conn)


def set_person_types(name: str, types: List[str], conn=None) -> None:
    """替换该人的全部类型关联；types[0] 设为主类型。同步 staff 镜像。"""
    nm = (name or "").strip()
    if not nm:
        raise StaffTypeError("姓名不能为空")
    own, conn = _own_conn(conn)
    try:
        conn.execute("DELETE FROM staff_type_map WHERE name=?", (nm,))
        for i, t in enumerate(types):
            t = (t or "").strip()
            if not t:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary, sort_order) "
                "VALUES(?,?,?,?)", (nm, t, 1 if i == 0 else 0, i))
        rp = conn.execute("SELECT hire_month, note FROM staff_roster WHERE name=?", (nm,)).fetchone()
        conn.commit()
    finally:
        _close(own, conn)


def add_person_type(name: str, type_name: str, conn=None) -> None:
    """给某人追加一个类型（已存在则忽略）。同步 staff 镜像。"""
    nm = (name or "").strip()
    tn = (type_name or "").strip()
    if not nm or not tn:
        raise StaffTypeError("姓名与类型均不能为空")
    own, conn = _own_conn(conn)
    try:
        exists = conn.execute(
            "SELECT 1 FROM staff_type_map WHERE name=? AND type_name=?", (nm, tn)).fetchone()
        if not exists:
            has_primary = conn.execute(
                "SELECT 1 FROM staff_type_map WHERE name=? AND is_primary=1", (nm,)).fetchone()
            conn.execute(
                "INSERT INTO staff_type_map(name, type_name, is_primary, sort_order) "
                "VALUES(?,?,?,?)", (nm, tn, 0 if has_primary else 1, 999))
            rp = conn.execute("SELECT hire_month, note FROM staff_roster WHERE name=?", (nm,)).fetchone()
            conn.commit()
    finally:
        _close(own, conn)


def remove_person_type(name: str, type_name: str, conn=None) -> None:
    """移除某人的一个类型关联；若删掉的是主类型则把剩余第一个提升为主类型。同步 staff 镜像。"""
    nm = (name or "").strip()
    tn = (type_name or "").strip()
    if not nm or not tn:
        raise StaffTypeError("姓名与类型均不能为空")
    own, conn = _own_conn(conn)
    try:
        conn.execute(
            "DELETE FROM staff_type_map WHERE name=? AND type_name=?", (nm, tn))
        if not conn.execute(
                "SELECT 1 FROM staff_type_map WHERE name=? AND is_primary=1", (nm,)).fetchone():
            first = conn.execute(
                "SELECT type_name FROM staff_type_map WHERE name=? "
                "ORDER BY sort_order, type_name", (nm,)).fetchone()
            if first:
                conn.execute(
                    "UPDATE staff_type_map SET is_primary=1 WHERE name=? AND type_name=?",
                    (nm, first["type_name"]))
        rp = conn.execute("SELECT hire_month, note FROM staff_roster WHERE name=?", (nm,)).fetchone()
        conn.commit()
    finally:
        _close(own, conn)


def sync_imported_staff(conn, name: str, stype: str, note: str = "") -> None:
    """导入职工清单时复用：保证花名册有此人、关联表有该类型（首类型即主类型），并同步 staff 镜像。

    仅用传入 conn（调用方负责 commit），不新开连接。姓名/类型为空则跳过。
    """
    nm = (name or "").strip()
    tn = (stype or "").strip()
    if not nm:
        return
    conn.execute("INSERT OR IGNORE INTO staff_roster(name, note) VALUES(?,?)",
                 (nm, (note or "").strip()))
    if tn:
        exists = conn.execute(
            "SELECT 1 FROM staff_type_map WHERE name=? AND type_name=?", (nm, tn)).fetchone()
        if not exists:
            has_primary = conn.execute(
                "SELECT 1 FROM staff_type_map WHERE name=? AND is_primary=1", (nm,)).fetchone()
            conn.execute(
                "INSERT INTO staff_type_map(name, type_name, is_primary, sort_order) "
                "VALUES(?,?,?,?)", (nm, tn, 0 if has_primary else 1, 0))
        rp = conn.execute("SELECT hire_month, note FROM staff_roster WHERE name=?", (nm,)).fetchone()
