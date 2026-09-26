"""员工类型维护 + 员工删除的引用检查

口径（已与需求方确认）：
- **员工类型表初始为空**：建库/首次打开不再预置任何类型（合伙/聘用/… 一律由用户在
  「员工类型」页自己新建，见 `ensure_defaults`）。`ensure_types()` 在导入职工清单时把
  清单里出现的未知类型**按需**补入，因此导入是唯一会自动建类型的路径。
- **参与结算由 staff_type_def.is_settle 控制**，**净额口径（net_basis）由
  '开票净额' | '收款净额' 两个枚举值表达**。
- person_settlement 改用 `settle_flags_of(name)` 读取，不再按类型名硬编码：
  按类型自己的 is_settle 决定是否计入、按 net_basis 决定按哪个金额计。
- **去写死（Plan A）**：类型名与角色（role_code → role_def）解耦。结算/收入/费用口径
  一律按角色解析，故**放开改名锁与内置删除锁**——改名/删除类型都不会断结算口径；
  唯一护栏是「有员工在用则禁止删除」（防止员工身份丢失→落入 other→收入判 0）。
  类型仍可各自设置 is_settle 与 net_basis（覆盖角色默认值）。

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

# ---------------------------------------------------------------------------
# 角色（去写死身份大类）：staff_type_def.role_code → role_def
# 运行期一切「身份口径」都走 role_code，绝不再按类型名子串坍缩。
# ---------------------------------------------------------------------------
ROLE_CODES = ("partner", "employee", "parttime", "other")


def ensure_roles(conn=None) -> None:
    """幂等建 role_def 表 + 填充种子（建库/升级兜底；init_db 已种，这里再保险一次）。

    手工复刻 schema 的内存库（大量测试用 :memory: 自己建表）并没有 role_def 表，
    故先 CREATE IF NOT EXISTS，否则下面的 INSERT OR IGNORE 会报 no such table。
    """
    own, c = _own_conn(conn)
    try:
        c.execute("CREATE TABLE IF NOT EXISTS role_def ("
                  "role_code TEXT PRIMARY KEY, label TEXT NOT NULL, "
                  "forbid_public_exclusive INTEGER NOT NULL DEFAULT 0, "
                  "include_in_income_report INTEGER NOT NULL DEFAULT 0, "
                  "report_class TEXT NOT NULL DEFAULT '其他', "
                  "default_net_basis TEXT NOT NULL DEFAULT '收款净额')")
        for code, label, fpe, iir, rc, dnb in (
            ("partner", "合伙", 1, 1, "合伙", "开票净额"),
            ("employee", "聘用", 1, 1, "聘用", "收款净额"),
            ("parttime", "兼职", 1, 1, "兼职", "收款净额"),
            ("other", "其他", 0, 0, "其他", "收款净额"),
        ):
            c.execute(
                "INSERT OR IGNORE INTO role_def(role_code,label,forbid_public_exclusive,"
                "include_in_income_report,report_class,default_net_basis) VALUES(?,?,?,?,?,?)",
                (code, label, fpe, iir, rc, dnb))
        c.commit()
    finally:
        _close(own, c)


def role_code_of(name: str, conn=None) -> str:
    """真实员工类型名 → 角色码（唯一运行期取身份口径入口，不再子串坍缩）。

    - 空名 / 类型不存在 → 'other'；
    - 否则取该类型 staff_type_def.role_code。
    改名任意类型都不会影响结果（口径按角色，不按名称）。
    """
    n = (name or "").strip()
    if not n:
        return "other"
    own, c = _own_conn(conn)
    try:
        r = c.execute("SELECT role_code FROM staff_type_def WHERE name=?", (n,)).fetchone()
        return (r["role_code"] or "other") if r else "other"
    finally:
        _close(own, c)


def role_label(role_code: str, conn=None) -> str:
    """角色码 → 显示名（对外展示用，不当 key）。"""
    code = (role_code or "").strip() or "other"
    own, c = _own_conn(conn)
    try:
        r = c.execute("SELECT label FROM role_def WHERE role_code=?", (code,)).fetchone()
        return r["label"] if r else code
    finally:
        _close(own, c)


def role_attr(role_code: str, conn=None) -> Dict:
    """角色全部属性（forbid_public_exclusive / include_in_income_report / report_class / default_net_basis）。

    缺码 → 返回 'other' 语义的兜底字典，绝不抛错（展示/校验可安全下行）。
    """
    code = (role_code or "").strip() or "other"
    own, c = _own_conn(conn)
    try:
        r = c.execute("SELECT * FROM role_def WHERE role_code=?", (code,)).fetchone()
        if not r:
            return {"role_code": code, "label": code, "forbid_public_exclusive": 0,
                    "include_in_income_report": 0, "report_class": "其他",
                    "default_net_basis": "收款净额"}
        return dict(r)
    finally:
        _close(own, c)


def identity_roles(conn=None):
    """结算分身份 / 收入报表的身份清单（角色码 → 显示名），按 include_in_income_report=1。

    去写死：不再硬编码 合伙/聘用/兼职，改用 role_def 配置（默认 partner/employee/parttime）。
    """
    own, c = _own_conn(conn)
    try:
        rows = c.execute(
            "SELECT role_code, label FROM role_def WHERE include_in_income_report=1 "
            "ORDER BY role_code").fetchall()
        return [(r["role_code"], r["label"]) for r in rows]
    finally:
        _close(own, c)


def list_roles(conn=None):
    """全部角色（角色码 → 显示名），用于下拉填充。"""
    own, c = _own_conn(conn)
    try:
        rows = c.execute("SELECT role_code, label FROM role_def ORDER BY role_code").fetchall()
        return [(r["role_code"], r["label"]) for r in rows]
    finally:
        _close(own, c)


def person_type_combo_items(conn=None):
    """身份下拉项（显示名, 角色码），含「未标」(空码)。供各 UI 编辑/筛选下拉共用。

    去写死：下拉不再硬编码 合伙/聘用/兼职，角色来自 role_def；存储值恒为角色码。
    """
    items = [("未标", "")]
    for code, label in list_roles(conn):
        items.append((label, code))
    return items


def role_label_map(conn=None):
    """角色码 → 显示名 字典（供只读表格把 person_type 翻成中文）。"""
    return {code: label for code, label in list_roles(conn)}


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
    """建库/升级后补齐 staff_type_def 的参与结算列（**只补列，绝不预置任何类型**）。

    行为边界（两条口径，勿再扩大）：
    - **不写行**：员工类型表初始为空，类型一律由用户在员工类型页自行新建；
      导入职工清单时的按需建类型走 `ensure_types()`。
    - **不回写已存在的行**：早期实现在每次调用时无条件
      `UPDATE ... SET is_settle=1 WHERE name IN (...)`，而 `list_types()` 又会每次
      调 `ensure_defaults()` —— 结果用户在页面上取消勾选后，下一次打开/刷新就被
      强行改回「参与结算」，表现为「勾选取消不掉」。修复：这里只做 schema 层面的
      幂等补列（老库缺 is_settle / net_basis 时补上，存量行落列默认值），
      对已有行**一个字段都不动**。
    """
    own, conn = _own_conn(conn)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]
        if "is_settle" not in cols:
            conn.execute(
                "ALTER TABLE staff_type_def ADD COLUMN is_settle INTEGER NOT NULL DEFAULT 0")
        if "net_basis" not in cols:
            conn.execute(
                "ALTER TABLE staff_type_def ADD COLUMN net_basis TEXT NOT NULL DEFAULT '收款净额'")
        conn.commit()
    finally:
        _close(own, conn)


def ensure_types(conn, names: List[str]) -> None:
    """导入职工清单时调用：把未知类型自动入库（is_builtin=0）。

    口径：**初始为空、按需创建**。这里只补「本次导入清单里出现、表里还没有」的类型，
    不预置任何常见类型、也不改动已有类型的 is_settle / net_basis（用户勾选为准）。
    """
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
    """类型清单：名称/是否内置/说明/排序/引用人数/是否参与计算/业务金额方式。

    表初始为空 → 首次打开返回 []，用户新建后才有行。
    返回行里的 net_basis **原样透出**（未参与结算时为 ''），不做兜底改写，
    供员工类型页直接显示。
    """
    own, conn = _own_conn(conn)
    try:
        ensure_defaults(conn)
        rows = conn.execute(
            """SELECT t.name AS name, t.is_builtin AS is_builtin, t.note AS note,
                      t.sort_order AS sort_order, t.is_settle AS is_settle,
                      t.net_basis AS net_basis, t.role_code AS role_code,
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
    - 若 name 直接命中 staff_type_def（类型名调用路径，如 staff_view 的 is_computable）
      → 取该类型 is_settle；
    - 否则按经办人姓名解析其员工类型（staff.staff_type）→ 再查 staff_type_def.is_settle；
    - 类型缺失/未参与 → False。
    （修复：详情保存/导入传入的是「经办人姓名」，旧实现误把姓名当类型名查 staff_type_def，
      导致方国兴=聘用这类理应参与结算的经办人被判为「未参与」。）
    """
    n = (name or "").strip()
    if not n:
        return False
    own, c = _own_conn(conn)
    try:
        # 1) 类型名直接命中（staff_type_def.name）
        r = c.execute("SELECT is_settle FROM staff_type_def WHERE name=?", (n,)).fetchone()
        if r is not None:
            return bool(r["is_settle"])
        # 2) 经办人姓名 → 解析其员工类型 → 再查类型表
        t = c.execute("SELECT staff_type FROM staff WHERE name=?", (n,)).fetchone()
        if t and (t["staff_type"] or "").strip():
            r = c.execute("SELECT is_settle FROM staff_type_def WHERE name=?",
                          (t["staff_type"].strip(),)).fetchone()
            return bool(r["is_settle"]) if r else False
        return False
    finally:
        _close(own, c)


def settle_flags_of(name: str, conn=None) -> Tuple[bool, str]:
    """(是否参与结算, 业务金额方式/「净额口径」) 的读数口径。

    - 名空 / 类型不存在 → (False, "")：无从依据的口径一律返回空，**不做臆造**；
    - 未参与结算 → (False, 库存的 net_basis 原样)：**不做 `or '收款净额'` 兜底**。
      否则「取消勾选时清空 net_basis」存下的空值会被悄悄还原成"收款净额"，
      用户以为清掉了其实还在，重新勾选就会沿用旧口径。
    - 参与结算 → (True, net_basis or '收款净额')：既然参与就必须有个口径。

    结算侧（person_settlement）只在 is_settle=True 时读第 2 项，故收窄兜底不影响金额。
    """
    n = (name or "").strip()
    if not n:
        return (False, "")
    own, c = _own_conn(conn)
    try:
        r = c.execute(
            "SELECT is_settle, net_basis FROM staff_type_def WHERE name=?", (n,)).fetchone()
        if not r:
            return (False, "")
        if not r["is_settle"]:
            return (False, r["net_basis"] or "")
        return (True, r["net_basis"] or "收款净额")
    finally:
        _close(own, c)


def set_settle(name: str, flag: bool, conn=None) -> None:
    """员工类型页开关回调：设置是否参与结算。

    只写 is_settle，**不碰 net_basis**：净额口径是用户自己选的配置，取消勾选
    只表达「这次不参与」，不该顺手删掉。清掉后重新勾选只能填默认值（收款净额），
    原口径被静默改写 → 金额按另一个口径算（合伙开票 100 万只收 60 万 → 少计 40 万）。
    库里留着原口径、页面显示留空，两者不冲突：is_settle=0 时结算侧根本不读 net_basis，
    金额完全不受影响。
    """
    own, c = _own_conn(conn)
    try:
        if flag:
            c.execute("UPDATE staff_type_def SET is_settle=1 WHERE name=?", (name,))
        else:
            c.execute("UPDATE staff_type_def SET is_settle=0 WHERE name=?", (name,))
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

    读取 staff_type_def.is_settle（参与结算是可设置的开关，见需求）：
    该类型被勾选参与则返回 True，未勾选/类型不存在返回 False。
    """
    return is_settle_participant(name, conn)


def add_type(name: str, note: str = "", role_code: str = "other", conn=None) -> None:
    name = (name or "").strip()
    if not name:
        raise StaffTypeError("类型名不能为空")
    if len(name) > 20:
        raise StaffTypeError("类型名过长（≤20 字符）")
    rc = (role_code or "other") if role_code in ROLE_CODES else "other"
    own, conn = _own_conn(conn)
    try:
        dup = conn.execute("SELECT 1 FROM staff_type_def WHERE name=?", (name,)).fetchone()
        if dup:
            raise StaffTypeError(f"类型已存在：{name}")
        nxt = conn.execute(
            "SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM staff_type_def").fetchone()["n"]
        conn.execute(
            "INSERT INTO staff_type_def(name, is_builtin, note, sort_order, role_code) VALUES(?,0,?,?,?)",
            (name, note.strip(), nxt, rc))
        conn.commit()
    finally:
        _close(own, conn)


def rename_type(old: str, new: str, conn=None) -> None:
    """改名：新名重复则报错；同步更新 staff 表的类型。

    去写死（Plan A）：不再按类型名硬编码结算口径 —— 类型名与角色码（role_code）解耦，
    改名后 role_code 不变、结算/收入/费用口径全部按角色解析，故**放开改名锁**
    （含原「内置三类禁止改名」），改名不会再断结算口径。
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
        conn.execute("UPDATE staff SET staff_type=? WHERE staff_type=?", (new, old))
        conn.commit()
    finally:
        _close(own, conn)


def set_role_code(name: str, code: str, conn=None) -> None:
    """改某员工类型的角色归属（partner/employee/parttime/other）。

    角色决定 forbid_public_exclusive / include_in_income_report / default_net_basis
    等语义；改名不影响角色，但用户可在此主动切换角色（如把一个自定义类型归为「合伙」）。
    非法码兜底为 'other'。
    """
    rc = (code or "other") if code in ROLE_CODES else "other"
    own, conn = _own_conn(conn)
    try:
        conn.execute("UPDATE staff_type_def SET role_code=? WHERE name=?", (rc, name))
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

    去写死（Plan A）：类型名与角色解耦，故**放开原「内置三类禁止删除」锁**；
    仅保留「有员工引用则禁删」这一真正的安全护栏（防止员工身份被改成空→结算落 other→收入 0）。
    """
    own, conn = _own_conn(conn)
    try:
        row = conn.execute("SELECT 1 FROM staff_type_def WHERE name=?", (name,)).fetchone()
        if not row:
            raise StaffTypeError(f"类型不存在：{name}")
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
