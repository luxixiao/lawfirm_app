"""费用类型维护：6 个分类 × 类内类型，顺序与归类影响结算表分组。

数据模型
- `expense_cat`（类型全集）：expense_type / category / sort_order（全局顺序）
- `expense_category`（分类说明）：name / note / sort_order

顺序口径
- **全局顺序 = 分类顺序 + 类型在类内的顺序**（save_layout 一次性写回 sort_order）。
- `ordered_types()` 供结算/年度聘用结算表取数，因此改动顺序即改导出列序。
- 分类固定 6 类（`CATEGORIES`），不可增删；说明文字可自定义填写。
- 「报销摊销等」为兜底分类（原「其他」改名），脏数据（分类不在 CATEGORIES 里）一律并入。
- 「公共专属费用」为公共专属支出分类：合伙/聘用/兼职 员工不可承担（导入校验，见 `validate_public_exclusive`）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.db import get_conn
from app.engine.staff_type import BUILTIN_TYPES  # 合伙/聘用/兼职（参与业务收入计算）

# 固定 6 类（顺序即展示顺序；不可增删，改动须同步 db.py 迁移与测试）
CATEGORIES = ["报酬发放", "住房公积金", "保险费", "汽油费", "报销摊销等", "公共专属费用"]
# 兜底分类：脏数据（分类不在 CATEGORIES 里）一律并入
FALLBACK_CATEGORY = "报销摊销等"

# 公共专属费用分类：合伙/聘用/兼职 这三类员工不可承担该分类下的支出
PUBLIC_EXCLUSIVE_CATEGORY = "公共专属费用"
_FORBIDDEN_STAFF_TYPES = set(BUILTIN_TYPES)  # {"合伙", "聘用", "兼职"}

# 预置默认归类规则（按类型名包含关键词，顺序敏感）
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


class ExpenseCatError(Exception):
    """费用类型维护的业务错误（提示给 UI）。"""


# ---------------------------------------------------------------------------
# 连接助手（conn 可注入，供内存库单测）
# ---------------------------------------------------------------------------

def _own_conn(conn=None):
    return conn is None, conn if conn is not None else get_conn()


def _close(own: bool, conn) -> None:
    if own:
        conn.close()


# ---------------------------------------------------------------------------
# 分类（固定 5 类，说明可自定义）
# ---------------------------------------------------------------------------

def ensure_categories(conn=None) -> None:
    """建库/升级后补齐 6 个分类（缺哪个补哪个）。"""
    own, conn = _own_conn(conn)
    try:
        for i, name in enumerate(CATEGORIES):
            conn.execute(
                "INSERT OR IGNORE INTO expense_category(name, note, sort_order)"
                " VALUES(?,?,?)", (name, "", i + 1))
        conn.commit()
    finally:
        _close(own, conn)


def list_categories(conn=None) -> List[Dict]:
    """分类清单：名称/说明/排序/该类的类型数。"""
    own, conn = _own_conn(conn)
    try:
        ensure_categories(conn)
        rows = conn.execute(
            """SELECT c.name AS name, c.note AS note, c.sort_order AS sort_order
               FROM expense_category c ORDER BY c.sort_order, c.name""").fetchall()
        counts = _count_by_category(conn)
        out = []
        for r in rows:
            d = dict(r)
            d["count"] = counts.get(d["name"], 0)
            out.append(d)
        # 分类表里若缺某个 CATEGORIES（被外部删过）→ 运行时补齐为 0 条
        have = {d["name"] for d in out}
        for i, name in enumerate(CATEGORIES):
            if name not in have:
                out.append({"name": name, "note": "", "sort_order": i + 1, "count": 0})
        return out
    finally:
        _close(own, conn)


def _count_by_category(conn) -> Dict[str, int]:
    m = _normalize(conn)
    out = {c: 0 for c in CATEGORIES}
    for r in conn.execute("SELECT category FROM expense_cat"):
        out[m.get(r["category"], FALLBACK_CATEGORY)] += 1
    return out


def _normalize(conn) -> Dict[str, str]:
    """脏数据归类（不在 CATEGORIES 内）→ 并入兜底分类；返回原值→规范的映射。"""
    return {c: (c if c in CATEGORIES else FALLBACK_CATEGORY)
            for c in {r["category"] for r in conn.execute("SELECT DISTINCT category FROM expense_cat")}}


def get_category_note(name: str, conn=None) -> str:
    own, conn = _own_conn(conn)
    try:
        r = conn.execute("SELECT note FROM expense_category WHERE name=?", (name,)).fetchone()
        return (r["note"] or "") if r else ""
    finally:
        _close(own, conn)


def set_category_note(name: str, note: str, conn=None) -> None:
    own, conn = _own_conn(conn)
    try:
        ensure_categories(conn)
        conn.execute("UPDATE expense_category SET note=? WHERE name=?", ((note or "").strip(), name))
        conn.commit()
    finally:
        _close(own, conn)


# ---------------------------------------------------------------------------
# 类型全集
# ---------------------------------------------------------------------------

def ensure_types(conn, types: list) -> None:
    """确保费用类型在 expense_cat 中；缺失的按默认规则自动归类"""
    for t in types:
        if not t:
            continue
        if conn.execute("SELECT 1 FROM expense_cat WHERE expense_type=?", (t,)).fetchone():
            continue
        cat = FALLBACK_CATEGORY
        for kw, c in _DEFAULT_RULE:
            if kw in t:
                cat = c
                break
        nxt = conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM expense_cat").fetchone()["n"]
        conn.execute("INSERT INTO expense_cat (expense_type, category, sort_order) VALUES (?,?,?)",
                     (t, cat, nxt))


def check_unknown(conn, types: list) -> list:
    """返回不在 expense_cat 名单中的费用类型（用于导入报错）"""
    known = {r["expense_type"] for r in conn.execute("SELECT expense_type FROM expense_cat")}
    return [t for t in types if t and t not in known]


def validate_public_exclusive(conn, items: list) -> list:
    """导入前校验：落入「公共专属费用」分类的支出，禁止由 合伙/聘用/兼职 员工承担。

    items：解析后的费用台账行（需含 `expense_type` / `actual_handler`）。
    返回违例人员清单（姓名(类型)，已去重升序）；为空表示通过。

    判定口径：
    - 费用类型本身 == PUBLIC_EXCLUSIVE_CATEGORY，或该类型归类 == PUBLIC_EXCLUSIVE_CATEGORY；
    - 且承担人（actual_handler）的结算身份 ∈ {合伙, 聘用, 兼职}（取自 staff 花名册）；
    - 「公共」「行政」等非花名册经办人（白名单）及未登记人员（落为「其他」）一律放行。
    """
    from app.engine.backfill import norm_type, staff_type_of
    cat_map = get_map(conn)
    viol = set()
    for it in items:
        et = (it.get("expense_type") or "").strip()
        handler = (it.get("actual_handler") or "").strip()
        if not et or not handler:
            continue
        cat = cat_map.get(et, FALLBACK_CATEGORY)
        if et == PUBLIC_EXCLUSIVE_CATEGORY or cat == PUBLIC_EXCLUSIVE_CATEGORY:
            pt = norm_type(staff_type_of(conn, handler))
            if pt in _FORBIDDEN_STAFF_TYPES:
                viol.add(f"{handler}({pt})")
    return sorted(viol)


def get_map(conn=None) -> dict:
    own, conn = _own_conn(conn)
    try:
        return {r["expense_type"]: r["category"] for r in conn.execute("SELECT * FROM expense_cat")}
    finally:
        _close(own, conn)


def get_by_category(category: str, conn=None) -> list:
    """该类下的费用类型（按类内维护顺序）。脏数据归类并入兜底类。"""
    own, conn = _own_conn(conn)
    try:
        m = _normalize(conn)
        cats = [c for c, v in m.items() if v == category]
        if not cats:
            return []
        ph = ",".join("?" * len(cats))
        rows = conn.execute(
            f"SELECT expense_type FROM expense_cat WHERE category IN ({ph})"
            f" ORDER BY sort_order, expense_type", cats).fetchall()
        return [r["expense_type"] for r in rows]
    finally:
        _close(own, conn)


def types_by_category(conn=None) -> Dict[str, List[str]]:
    """{分类: [类型...]}，类内按维护顺序；保证 5 个分类都有键。"""
    own, conn = _own_conn(conn)
    try:
        ensure_categories(conn)
        out: Dict[str, List[str]] = {c: [] for c in CATEGORIES}
        for r in conn.execute(
                "SELECT expense_type, category FROM expense_cat ORDER BY sort_order, expense_type"):
            out[_norm_cat(r["category"])].append(r["expense_type"])
        return out
    finally:
        _close(own, conn)


def _norm_cat(category: Optional[str]) -> str:
    return category if category in CATEGORIES else FALLBACK_CATEGORY


def ordered_types(conn=None) -> list:
    """按维护顺序（分类顺序 + 类内顺序）返回费用类型列表。"""
    own, conn = _own_conn(conn)
    try:
        by_cat = types_by_category(conn)
        return [t for c in CATEGORIES for t in by_cat.get(c, [])]
    finally:
        _close(own, conn)


def add_type(expense_type: str, category: str = FALLBACK_CATEGORY, conn=None) -> None:
    """新增类型；重名报错。新类型追加到该类末尾。"""
    name = (expense_type or "").strip()
    if not name:
        raise ExpenseCatError("费用类型名称不能为空")
    if len(name) > 30:
        raise ExpenseCatError("费用类型名称过长（≤30 字符）")
    cat = _norm_cat(category)
    own, conn = _own_conn(conn)
    try:
        if conn.execute("SELECT 1 FROM expense_cat WHERE expense_type=?", (name,)).fetchone():
            raise ExpenseCatError(f"费用类型已存在：{name}")
        nxt = conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM expense_cat").fetchone()["n"]
        conn.execute("INSERT INTO expense_cat (expense_type, category, sort_order) VALUES (?,?,?)",
                     (name, cat, nxt))
        conn.commit()
    finally:
        _close(own, conn)


def rename_type(old: str, new: str, conn=None) -> None:
    """改名：同步更新费用台账，避免历史数据归类丢失。"""
    new = (new or "").strip()
    if not new:
        raise ExpenseCatError("费用类型名称不能为空")
    if len(new) > 30:
        raise ExpenseCatError("费用类型名称过长（≤30 字符）")
    own, conn = _own_conn(conn)
    try:
        if not conn.execute("SELECT 1 FROM expense_cat WHERE expense_type=?", (old,)).fetchone():
            raise ExpenseCatError(f"费用类型不存在：{old}")
        if new != old and conn.execute(
                "SELECT 1 FROM expense_cat WHERE expense_type=?", (new,)).fetchone():
            raise ExpenseCatError(f"费用类型已存在：{new}")
        conn.execute("UPDATE expense_cat SET expense_type=? WHERE expense_type=?", (new, old))
        conn.execute("UPDATE expense_ledger SET expense_type=? WHERE expense_type=?", (new, old))
        # 别名的 canonical 跟随改名，避免 dangling 别名
        conn.execute("UPDATE expense_type_alias SET canonical=? WHERE canonical=?", (new, old))
        conn.commit()
    finally:
        _close(own, conn)


def set_category(expense_type: str, category: str, conn=None) -> None:
    """改归类（新分类非法时并入兜底类）。"""
    own, conn = _own_conn(conn)
    try:
        conn.execute("UPDATE expense_cat SET category=? WHERE expense_type=?",
                     (_norm_cat(category), expense_type))
        conn.commit()
    finally:
        _close(own, conn)


def type_reference_count(expense_type: str, conn=None) -> int:
    """该类型在费用台账中的使用条数（删除前提示用）。"""
    own, conn = _own_conn(conn)
    try:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM expense_ledger WHERE expense_type=?",
            (expense_type,)).fetchone()["n"]
    finally:
        _close(own, conn)


def delete_type(expense_type: str, conn=None) -> None:
    """删除类型（仅删配置，不动历史台账；调用方应先提示引用条数）。

    同时删除指向该类型的全部别名（避免出现 dangling 别名）。
    """
    own, conn = _own_conn(conn)
    try:
        cur = conn.execute("DELETE FROM expense_cat WHERE expense_type=?", (expense_type,))
        if cur.rowcount == 0:
            raise ExpenseCatError(f"费用类型不存在：{expense_type}")
        conn.execute("DELETE FROM expense_type_alias WHERE canonical=?", (expense_type,))
        conn.commit()
    finally:
        _close(own, conn)


# ---------------------------------------------------------------------------
# 别名（同义归一）：台账写法 → 规范类型
# ---------------------------------------------------------------------------

def resolve_type(name: str, conn=None) -> Optional[str]:
    """把费用类型名解析为规范类型名。

    - 已是规范类型（在 expense_cat 中）→ 原样返回；
    - 是某别名的写法 → 返回其 canonical；
    - 两者都不是 → 返回 None（真正的未知类型）。
    导入门禁据此把「公积金」归一成「住房公积金」再校验/写库。
    """
    name = (name or "").strip()
    if not name:
        return None
    own, conn = _own_conn(conn)
    try:
        if conn.execute("SELECT 1 FROM expense_cat WHERE expense_type=?", (name,)).fetchone():
            return name
        r = conn.execute("SELECT canonical FROM expense_type_alias WHERE alias=?", (name,)).fetchone()
        if r:
            return r["canonical"]
        return None
    finally:
        _close(own, conn)


def add_alias(canonical: str, alias: str, conn=None) -> None:
    """为规范类型新增一个同义别名（如 canonical='住房公积金', alias='公积金'）。"""
    canonical = (canonical or "").strip()
    alias = (alias or "").strip()
    if not alias:
        raise ExpenseCatError("别名不能为空")
    if len(alias) > 30:
        raise ExpenseCatError("别名过长（≤30 字符）")
    own, conn = _own_conn(conn)
    try:
        if not conn.execute("SELECT 1 FROM expense_cat WHERE expense_type=?", (canonical,)).fetchone():
            raise ExpenseCatError(f"规范类型不存在：{canonical}")
        # 别名不能与已有规范类型重名（否则归一会歧义）
        if conn.execute("SELECT 1 FROM expense_cat WHERE expense_type=?", (alias,)).fetchone():
            raise ExpenseCatError(f"别名与已有费用类型重名：{alias}")
        if conn.execute("SELECT 1 FROM expense_type_alias WHERE alias=?", (alias,)).fetchone():
            raise ExpenseCatError(f"别名已存在：{alias}")
        conn.execute("INSERT INTO expense_type_alias(alias, canonical) VALUES(?,?)", (alias, canonical))
        conn.commit()
    finally:
        _close(own, conn)


def delete_alias(alias: str, conn=None) -> None:
    """删除一个别名。"""
    alias = (alias or "").strip()
    own, conn = _own_conn(conn)
    try:
        cur = conn.execute("DELETE FROM expense_type_alias WHERE alias=?", (alias,))
        if cur.rowcount == 0:
            raise ExpenseCatError(f"别名不存在：{alias}")
        conn.commit()
    finally:
        _close(own, conn)


def list_aliases(conn=None) -> List[Dict]:
    """全部别名：[{alias, canonical}]（按 canonical, alias 排序）。"""
    own, conn = _own_conn(conn)
    try:
        return [{"alias": r["alias"], "canonical": r["canonical"]}
                for r in conn.execute(
                    "SELECT alias, canonical FROM expense_type_alias ORDER BY canonical, alias")]
    finally:
        _close(own, conn)


def aliases_by_canonical(conn=None) -> Dict[str, List[str]]:
    """{规范类型: [别名...]}，供 UI 在类型卡内按类型分组展示。"""
    out: Dict[str, List[str]] = {}
    for r in list_aliases(conn):
        out.setdefault(r["canonical"], []).append(r["alias"])
    return out


def sync_from_ledger(conn=None) -> None:
    """从费用台账同步全部类型到 expense_cat（按默认规则自动归类）"""
    own, conn = _own_conn(conn)
    try:
        types = [r["expense_type"] for r in conn.execute(
            "SELECT DISTINCT expense_type FROM expense_ledger WHERE expense_type IS NOT NULL")]
        ensure_types(conn, types)
        conn.commit()
    finally:
        _close(own, conn)


# ---------------------------------------------------------------------------
# 顺序：全局上移/下移、类内上移/下移、拖拽落库
# ---------------------------------------------------------------------------

def move_in_category(expense_type: str, direction: int, conn=None) -> bool:
    """类内上移(-1)/下移(+1)：只在同分类内交换，跨分类不动。返回是否成功。"""
    own, conn = _own_conn(conn)
    try:
        by_cat = types_by_category(conn)
        cat = _current_category(conn, expense_type)
        names = by_cat.get(cat, [])
        if expense_type not in names:
            return False
        i = names.index(expense_type)
        j = i + direction
        if j < 0 or j >= len(names):
            return False
        names[i], names[j] = names[j], names[i]
        layout = dict(by_cat)
        layout[cat] = names
        _write_layout(conn, layout)
        conn.commit()
        return True
    finally:
        _close(own, conn)


def _current_category(conn, expense_type: str) -> str:
    r = conn.execute("SELECT category FROM expense_cat WHERE expense_type=?", (expense_type,)).fetchone()
    return _norm_cat(r["category"]) if r else FALLBACK_CATEGORY


def _write_layout(conn, layout: Dict[str, List[str]]) -> None:
    """按 {分类: [类型...]} 写回 category 与全局 sort_order。"""
    seq = 0
    for cat in CATEGORIES:
        for t in layout.get(cat, []):
            seq += 1
            conn.execute("UPDATE expense_cat SET category=?, sort_order=? WHERE expense_type=?",
                         (cat, seq, t))
    # 漏网（layout 未覆盖的类型）保持原归类，接到末尾
    covered = {t for v in layout.values() for t in v}
    for r in conn.execute("SELECT expense_type, category FROM expense_cat ORDER BY sort_order, expense_type"):
        if r["expense_type"] not in covered:
            seq += 1
            conn.execute("UPDATE expense_cat SET sort_order=? WHERE expense_type=?",
                         (seq, r["expense_type"]))


def save_layout(layout: Dict[str, List[str]], conn=None) -> None:
    """拖拽/排序后一次性落库：归类 + 类内顺序（分类顺序固定为 CATEGORIES）。"""
    own, conn = _own_conn(conn)
    try:
        _write_layout(conn, layout)
        conn.commit()
    finally:
        _close(own, conn)


# ---------------------------------------------------------------------------
# 配置整表导出 / 导入（Excel 备份迁移用）
# ---------------------------------------------------------------------------

def export_all(conn=None) -> Dict:
    """导出全部费用类型配置：分类说明 + 类型归类 + 全局顺序 + 别名。"""
    own, conn = _own_conn(conn)
    try:
        cats = [{"name": c["name"], "note": c["note"] or "", "sort_order": c["sort_order"]}
                for c in list_categories(conn)]
        types = [{"expense_type": r["expense_type"], "category": r["category"],
                  "sort_order": r["sort_order"]}
                 for r in conn.execute(
                     "SELECT expense_type, category, sort_order FROM expense_cat ORDER BY sort_order")]
        aliases = list_aliases(conn)
        return {"categories": cats, "types": types, "aliases": aliases}
    finally:
        _close(own, conn)


def import_all(data: Dict, conn=None) -> None:
    """整表替换费用类型配置（分类说明 + 类型归类 + 顺序）。

    文件校验：同一费用类型在导入文件中出现多次时直接报错拦截，
    提示用户修改 Excel 后再导入（不做静默跳过）。
    """
    # 先校验：文件中同一费用类型出现多次 → 拦截，提示先修改 Excel
    seen: set = set()
    dupes: List[str] = []
    for t in data.get("types", []):
        name = str(t.get("expense_type", "")).strip()
        if not name:
            continue
        if name in seen and name not in dupes:
            dupes.append(name)
        seen.add(name)
    if dupes:
        raise ExpenseCatError(
            "导入文件中的费用类型存在重复（同名只能出现一次），请修改 Excel 后重新导入：\n"
            + "、".join(dupes))

    own, conn = _own_conn(conn)
    try:
        conn.execute("DELETE FROM expense_cat")
        conn.execute("DELETE FROM expense_category")
        conn.execute("DELETE FROM expense_type_alias")
        for c in data.get("categories", []):
            conn.execute("INSERT INTO expense_category(name, note, sort_order) VALUES(?,?,?)",
                         (str(c["name"]), str(c.get("note", "") or ""), int(c.get("sort_order", 0) or 0)))
        known_types: set = set()
        for t in data.get("types", []):
            name = str(t["expense_type"])
            known_types.add(name)
            conn.execute("INSERT INTO expense_cat(expense_type, category, sort_order) VALUES(?,?,?)",
                         (name,
                          str(t.get("category", FALLBACK_CATEGORY) or FALLBACK_CATEGORY),
                          int(t.get("sort_order", 0) or 0)))
        ensure_categories(conn)   # 兜底：保证 5 个固定分类存在
        # 别名：canonical 必须是刚写入的规范类型，否则报错拦截（避免 dangling 别名）
        for a in data.get("aliases", []):
            alias = str(a.get("alias", "")).strip()
            canonical = str(a.get("canonical", "")).strip()
            if not alias or not canonical:
                continue
            if canonical not in known_types:
                raise ExpenseCatError(
                    f"别名「{alias}」指向的规范类型不存在：{canonical}（请先在该类型所在分类添加）")
            conn.execute("INSERT INTO expense_type_alias(alias, canonical) VALUES(?,?)",
                         (alias, canonical))
        conn.commit()
    finally:
        _close(own, conn)
