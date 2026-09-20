"""会计科目维护：一级科目（level=1）与二级科目（level=2，挂在某个一级下）。

数据模型
- `account_subject`：id / level / parent_id / name / code / sort_order / note
- 树形归属：二级科目的 parent_id 指向所属一级科目 id；一级科目 parent_id 为 NULL。
- 排序：一级科目按全局 sort_order；二级科目按 parent_id + sort_order（即「所属一级内」的顺序）。

顺序口径
- 页面拖拽/上移下移直接写回 sort_order（`save_layout_by_ids` 一次性按 id 落库整棵树）。
- 校验：费用台账导入时，逐行核对 (subject1, subject2) 是否都存在于本表；
  未知科目阻断导入（见 `validate_ledger_subjects`），与「费用类型」校验同范式。
- 主表从空起步：不自动从费用台账 harvest；旧台账科目不参与校验，仅在「账面情况」按账期汇总时如实展示。
- 改名级联更新费用台账的 subject1/subject2（与「费用类型」改名同步台账一致），删除只删主表配置、
  不动历史台账（历史台账仍保留原科目文字，重导入时由校验发现）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.db import get_conn


class AccountSubjectError(Exception):
    """会计科目维护的业务错误（提示给 UI）。"""


# ---------------------------------------------------------------------------
# 连接助手（conn 可注入，供内存库单测）
# ---------------------------------------------------------------------------

def _own_conn(conn=None):
    return conn is None, conn if conn is not None else get_conn()


def _close(own: bool, conn) -> None:
    if own:
        conn.close()


def _next_order(conn, level: int, parent_id: Optional[int] = None) -> int:
    if level == 1:
        r = conn.execute(
            "SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM account_subject WHERE level=1").fetchone()
    else:
        r = conn.execute(
            "SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM account_subject "
            "WHERE level=2 AND parent_id=?", (parent_id,)).fetchone()
    return r["n"]


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def get_tree(conn=None) -> List[Dict]:
    """返回 [ {id, name, code, note, sort_order, children:[...]}, ... ]，按一级顺序、二级顺序。"""
    own, conn = _own_conn(conn)
    try:
        parents = [dict(r) for r in conn.execute(
            "SELECT id, name, code, note, sort_order FROM account_subject "
            "WHERE level=1 ORDER BY sort_order, name")]
        out = []
        for p in parents:
            kids = [dict(r) for r in conn.execute(
                "SELECT id, name, code, note, sort_order FROM account_subject "
                "WHERE level=2 AND parent_id=? ORDER BY sort_order, name", (p["id"],))]
            out.append({
                "id": p["id"], "name": p["name"], "code": p.get("code") or "",
                "note": p.get("note") or "", "sort_order": p["sort_order"],
                "children": kids,
            })
        return out
    finally:
        _close(own, conn)


def subject_sets(conn=None) -> Tuple[set, Dict[str, set]]:
    """返回 (一级科目名集合, {一级名: 二级名集合})，供校验/页面使用。"""
    own, conn = _own_conn(conn)
    try:
        l1 = {r["name"] for r in conn.execute("SELECT name FROM account_subject WHERE level=1")}
        l2: Dict[str, set] = {n: set() for n in l1}
        for r in conn.execute(
                "SELECT s.name AS c, p.name AS p FROM account_subject s "
                "JOIN account_subject p ON s.parent_id=p.id WHERE s.level=2"):
            l2.setdefault(r["p"], set()).add(r["c"])
        return l1, l2
    finally:
        _close(own, conn)


# ---------------------------------------------------------------------------
# 增
# ---------------------------------------------------------------------------

def add_level1(name: str, code: str = "", note: str = "", conn=None) -> int:
    """新增一级科目；重名报错。"""
    name = (name or "").strip()
    if not name:
        raise AccountSubjectError("一级科目名称不能为空")
    if len(name) > 30:
        raise AccountSubjectError("一级科目名称过长（≤30 字符）")
    own, conn = _own_conn(conn)
    try:
        if conn.execute("SELECT 1 FROM account_subject WHERE level=1 AND name=?", (name,)).fetchone():
            raise AccountSubjectError(f"一级科目已存在：{name}")
        so = _next_order(conn, 1)
        cur = conn.execute(
            "INSERT INTO account_subject(level, parent_id, name, code, sort_order, note) "
            "VALUES(1, NULL, ?, ?, ?, ?)", (name, code, so, note))
        conn.commit()
        return cur.lastrowid
    finally:
        _close(own, conn)


def add_level2(parent_id: int, name: str, code: str = "", note: str = "", conn=None) -> int:
    """在指定一级科目下新增二级科目；重名（同父内）报错。"""
    name = (name or "").strip()
    if not name:
        raise AccountSubjectError("二级科目名称不能为空")
    if len(name) > 30:
        raise AccountSubjectError("二级科目名称过长（≤30 字符）")
    own, conn = _own_conn(conn)
    try:
        p = conn.execute("SELECT id FROM account_subject WHERE id=? AND level=1", (parent_id,)).fetchone()
        if not p:
            raise AccountSubjectError("所属一级科目不存在")
        if conn.execute(
                "SELECT 1 FROM account_subject WHERE level=2 AND parent_id=? AND name=?",
                (parent_id, name)).fetchone():
            raise AccountSubjectError(f"该一级科目下已存在二级科目：{name}")
        so = _next_order(conn, 2, parent_id)
        cur = conn.execute(
            "INSERT INTO account_subject(level, parent_id, name, code, sort_order, note) "
            "VALUES(2, ?, ?, ?, ?, ?)", (parent_id, name, code, so, note))
        conn.commit()
        return cur.lastrowid
    finally:
        _close(own, conn)


# ---------------------------------------------------------------------------
# 改（改名级联台账；移动/重排见下方）
# ---------------------------------------------------------------------------

def rename(subject_id: int, new_name: str, conn=None) -> None:
    """改名：一级改名级联更新费用台账 subject1；二级改名级联 subject2。重名报错。"""
    new_name = (new_name or "").strip()
    if not new_name:
        raise AccountSubjectError("科目名称不能为空")
    if len(new_name) > 30:
        raise AccountSubjectError("科目名称过长（≤30 字符）")
    own, conn = _own_conn(conn)
    try:
        row = conn.execute(
            "SELECT id, level, parent_id, name FROM account_subject WHERE id=?", (subject_id,)).fetchone()
        if not row:
            raise AccountSubjectError("科目不存在")
        old = row["name"]
        if new_name == old:
            return
        if row["level"] == 1:
            if conn.execute(
                    "SELECT 1 FROM account_subject WHERE level=1 AND name=? AND id!=?",
                    (new_name, subject_id)).fetchone():
                raise AccountSubjectError(f"一级科目已存在：{new_name}")
        else:
            if conn.execute(
                    "SELECT 1 FROM account_subject WHERE level=2 AND parent_id=? AND name=? AND id!=?",
                    (row["parent_id"], new_name, subject_id)).fetchone():
                raise AccountSubjectError(f"该一级科目下已存在二级科目：{new_name}")
        conn.execute("UPDATE account_subject SET name=? WHERE id=?", (new_name, subject_id))
        if row["level"] == 1:
            conn.execute("UPDATE expense_ledger SET subject1=? WHERE subject1=?", (new_name, old))
        else:
            conn.execute("UPDATE expense_ledger SET subject2=? WHERE subject2=?", (new_name, old))
        conn.commit()
    finally:
        _close(own, conn)


def delete(subject_id: int, conn=None) -> None:
    """删除科目：一级科目级联删除其下二级科目（仅删主表配置，不动费用台账历史数据）。"""
    own, conn = _own_conn(conn)
    try:
        row = conn.execute(
            "SELECT id, level FROM account_subject WHERE id=?", (subject_id,)).fetchone()
        if not row:
            return
        if row["level"] == 1:
            conn.execute("DELETE FROM account_subject WHERE parent_id=?", (subject_id,))
        conn.execute("DELETE FROM account_subject WHERE id=?", (subject_id,))
        conn.commit()
    finally:
        _close(own, conn)


# ---------------------------------------------------------------------------
# 顺序：上移/下移、跨父搬运（拖拽落库见 save_layout）
# ---------------------------------------------------------------------------

def _level1_ids(conn) -> List[int]:
    return [r["id"] for r in conn.execute(
        "SELECT id FROM account_subject WHERE level=1 ORDER BY sort_order, name")]


def _level2_ids(conn, parent_id: int) -> List[int]:
    return [r["id"] for r in conn.execute(
        "SELECT id FROM account_subject WHERE level=2 AND parent_id=? ORDER BY sort_order, name",
        (parent_id,))]


def _write_order(conn, ids: List[int]) -> None:
    for seq, sid in enumerate(ids, 1):
        conn.execute("UPDATE account_subject SET sort_order=? WHERE id=?", (seq, sid))


def move_level1(subject_id: int, direction: int, conn=None) -> bool:
    """一级科目上移(-1)/下移(+1)。返回是否成功。"""
    own, conn = _own_conn(conn)
    try:
        ids = _level1_ids(conn)
        if subject_id not in ids:
            return False
        i = ids.index(subject_id)
        j = i + direction
        if j < 0 or j >= len(ids):
            return False
        ids[i], ids[j] = ids[j], ids[i]
        _write_order(conn, ids)
        conn.commit()
        return True
    finally:
        _close(own, conn)


def move_level2(subject_id: int, direction: int, conn=None) -> bool:
    """二级科目在其所属一级内上移(-1)/下移(+1)。返回是否成功。"""
    own, conn = _own_conn(conn)
    try:
        row = conn.execute(
            "SELECT parent_id FROM account_subject WHERE id=? AND level=2", (subject_id,)).fetchone()
        if not row:
            return False
        ids = _level2_ids(conn, row["parent_id"])
        if subject_id not in ids:
            return False
        i = ids.index(subject_id)
        j = i + direction
        if j < 0 or j >= len(ids):
            return False
        ids[i], ids[j] = ids[j], ids[i]
        _write_order(conn, ids)
        conn.commit()
        return True
    finally:
        _close(own, conn)


def reparent_level2(subject_id: int, new_parent_id: int, index: int, conn=None) -> None:
    """把二级科目移动到另一个一级科目下的 index 位置（拖拽跨卡片 = 改归属）。"""
    own, conn = _own_conn(conn)
    try:
        if subject_id == new_parent_id:
            return
        p = conn.execute("SELECT id FROM account_subject WHERE id=? AND level=1", (new_parent_id,)).fetchone()
        if not p:
            raise AccountSubjectError("目标一级科目不存在")
        cur = conn.execute(
            "SELECT name, parent_id FROM account_subject WHERE id=?", (subject_id,)).fetchone()
        if conn.execute(
                "SELECT 1 FROM account_subject WHERE level=2 AND parent_id=? AND name=? AND id!=?",
                (new_parent_id, cur["name"], subject_id)).fetchone():
            raise AccountSubjectError(f"目标一级科目下已存在二级科目：{cur['name']}")
        kids = [r["id"] for r in conn.execute(
            "SELECT id FROM account_subject WHERE level=2 AND parent_id=? ORDER BY sort_order, name",
            (new_parent_id,))]
        if cur["parent_id"] == new_parent_id:
            kids = [k for k in kids if k != subject_id]
        index = max(0, min(index, len(kids)))
        kids.insert(index, subject_id)
        conn.execute("UPDATE account_subject SET parent_id=? WHERE id=?", (new_parent_id, subject_id))
        _write_order(conn, kids)
        conn.commit()
    finally:
        _close(own, conn)


def save_layout_by_ids(tree: List[Dict], conn=None) -> None:
    """按 id 落库整棵树：tree = [ {"id": 一级id, "children": [二级id, ...]}, ... ]（按展示顺序）。

    一级按 tree 顺序写 sort_order；二级按 children 顺序写 parent_id + sort_order。
    用 id 定位，绝不新建或重复；跨父搬运由 parent_id 直接更新完成。
    改名请走 `rename`，删除请走 `delete`，本函数只负责顺序与归属。
    """
    own, conn = _own_conn(conn)
    try:
        seq1 = 0
        for p in tree:
            pid = p["id"]
            seq1 += 1
            conn.execute("UPDATE account_subject SET sort_order=? WHERE id=?", (seq1, pid))
            seq2 = 0
            for cid in p.get("children", []):
                seq2 += 1
                conn.execute(
                    "UPDATE account_subject SET parent_id=?, sort_order=? WHERE id=?",
                    (pid, seq2, cid))
        conn.commit()
    finally:
        _close(own, conn)


# ---------------------------------------------------------------------------
# 主表整表导入 / 导出（xlsx 备份迁移用，单行格式见视图层）
# ---------------------------------------------------------------------------

def export_rows(conn=None) -> List[Dict]:
    """导出为扁平行：每行 {level1, level2, code, note}。一级行 level2 为空。"""
    own, conn = _own_conn(conn)
    try:
        out = []
        for p in get_tree(conn):
            out.append({"level1": p["name"], "level2": "", "code": p.get("code") or "",
                        "note": p.get("note") or ""})
            for c in p["children"]:
                out.append({"level1": p["name"], "level2": c["name"], "code": c.get("code") or "",
                            "note": c.get("note") or ""})
        return out
    finally:
        _close(own, conn)


def import_rows(rows: List[Dict], conn=None) -> None:
    """从扁平行整表替换科目主表（仅删主表配置，不动费用台账历史数据）。
    rows: [{level1, level2?, code?, note?}, ...]
    """
    own, conn = _own_conn(conn)
    try:
        conn.execute("DELETE FROM account_subject")
        l1_ids: Dict[str, int] = {}
        for r in rows:
            l1 = (r.get("level1") or "").strip()
            if not l1:
                continue
            if l1 not in l1_ids:
                l1_ids[l1] = conn.execute(
                    "INSERT INTO account_subject(level, parent_id, name, code, note, sort_order) "
                    "VALUES(1, NULL, ?, ?, ?, ?)",
                    (l1, r.get("code") or "", r.get("note") or "", len(l1_ids) + 1)).lastrowid
            l2 = (r.get("level2") or "").strip()
            if l2:
                conn.execute(
                    "INSERT INTO account_subject(level, parent_id, name, code, note, sort_order) "
                    "VALUES(2, ?, ?, ?, ?, ?)",
                    (l1_ids[l1], l2, r.get("code") or "", r.get("note") or "", 0))
        conn.commit()
    finally:
        _close(own, conn)


# ---------------------------------------------------------------------------
# 导入校验
# ---------------------------------------------------------------------------

def validate_ledger_subjects(rows: List[Dict], conn=None) -> List[Tuple[str, str]]:
    """校验费用台账行里的 (subject1, subject2) 是否都在会计科目主表中。

    返回未知组合的 dedupe 列表 [(subject1, subject2), ...]；空表示通过。
    - subject1 必须是一级科目；
    - 若 subject2 非空，必须是该一级科目下的二级科目。
    """
    own, conn = _own_conn(conn)
    try:
        l1, l2 = subject_sets(conn)
        bad = set()
        for it in rows:
            s1 = (it.get("subject1") or "").strip()
            s2 = (it.get("subject2") or "").strip()
            if not s1:
                if s2:
                    bad.add((s1, s2))
                continue
            if s1 not in l1:
                bad.add((s1, s2))
                continue
            if s2 and s2 not in l2.get(s1, set()):
                bad.add((s1, s2))
        return sorted(bad)
    finally:
        _close(own, conn)
