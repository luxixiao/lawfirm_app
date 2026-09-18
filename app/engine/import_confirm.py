"""导入时「确认」留痕 —— 让「导入后」模式不再重报已处理的疑问（批 3-1）

问题（为什么需要这个模块）
--------------------------
四态里有两类疑问**只能靠人点「确认」消掉**：

1. `import_confidence.evaluate()` 的**兜底低置信**（解析口径不确定，须人过目）；
2. `import_confidence.red_orig_diff()` 的**红字 ⇄ 蓝字原票不一致**。

而 `UnifiedImportDialog._confirmed` 只是一个**内存下标集合**，库里**零痕迹**。
批 1a 把 `accept()` 升级为硬拦后（「待补录 / 待确认」必须清零才能入库），
**入库那一刻这些疑问必然都已逐行消掉**；于是「导入后」模式再从 `raw_ledger`
重推四态时，会把它们**原样重新报成「待确认」**——而批 1b 的 post 模式隐藏了
「确认」按钮 ⇒ 用户**点不掉的假待办**。（批 2 把「台账 ⇄ 销项/库」比对判进
待确认后，这条会成为放大器：批 2 一上线就开始制造同类假待办。）

落点：复用既有的 `anomaly_note` 表（**零 schema**）
--------------------------------------------------
`db.py` 里早有 `anomaly_note(invoice_no, dim, period, note, confirmed_at)`，
主键 `(invoice_no, dim, period)`，`dim` 为 TEXT ⇒ **新增一个 dim 取值不需要建表**。
它原本只被**旧「导入后」页**的「标记已确认异常」写（`review_post_view`，`dim='merged'`），
由 `review_compare.load_confirmed_notes(period)` 读回，用于压制已确认的差异。

本模块用**独立 dim `import_confirm`**，与旧页的人工备注 `merged` **互不覆盖**：

- 写入：`commit_ledger_import` 在**同一事务**内先清本账期本 dim、再逐票写；
  覆盖式重导同一账期时旧确认自然失效（不会残留失效的确认去压制新一批的疑问）。
- 读取：`load_confirmations(period)` 优先本 dim，再回退旧页 `merged` 与更老的
  `handler`/`received` 维度（与 `review_compare.load_confirmed_notes` 同口径，
  批 4 删旧页时正好收口到本模块）。
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

from app.db import get_conn

# 本模块写入/读取的维度值。刻意与旧「导入后」页的 'merged' 分开：
# 那个是**人在导入后**手工标记的异常备注（本模块不碰），
# 这个是**导入那一刻**点过「确认」的留痕（归台账批次，随覆盖式重导整体失效）。
CONFIRM_DIM = "import_confirm"

# 批 3-2b：导入时**就地修改**的字段留痕（另一独立 dim，与确认留痕互不覆盖）。
# 为什么需要：sheet3「在库」行的文本字段（购方 / 经办人分摊 / 案号…）在导入时被改过后，
# 库内**零落点**（收款走 A10 落 collection，文本不写任何表）→「导入后」模式从
# raw_ledger（原文镜表）重建时会**无声显示旧值**，用户会以为改动丢了。
# 留痕只记「改了哪些字段」，不记新值 —— 镜表=原文的铁律不破坏，回写仍走
# `review_writeback.apply_edit`（批 3 落点）。
EDIT_DIM = "import_edit"

# 旧页写入的维度（读取时作为回退来源，批 4 删旧页后仍保留兼容历史数据）
_LEGACY_DIMS = ("merged", "handler", "received")

NOTE_PREFIX = "导入时确认"


def clear_confirmations(conn, period: str) -> int:
    """清掉该账期**本模块**（`import_confirm`）的确认留痕；返回删除条数。

    只删自己的 dim —— 旧「导入后」页写的 `merged` 等人工备注**绝不动**。
    """
    cur = conn.execute(
        "DELETE FROM anomaly_note WHERE period=? AND dim=?", (period, CONFIRM_DIM))
    return cur.rowcount or 0


def save_confirmations(conn, period: str, items: Optional[Iterable[Dict]]) -> int:
    """把导入时点过「确认」的行写进 `anomaly_note`（调用方保证同一事务）。

    items: `[{"invoice_no": str, "note": str}, ...]`；票号为空的行跳过。
    同票同账期靠主键 `INSERT OR REPLACE` 幂等。返回写入条数。
    """
    n = 0
    for it in items or []:
        no = (it.get("invoice_no") or "").strip()
        if not no:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO anomaly_note "
            "(invoice_no, dim, period, note, confirmed_at) "
            "VALUES (?,?,?,?, datetime('now','localtime'))",
            (no, CONFIRM_DIM, period, (it.get("note") or "").strip()),
        )
        n += 1
    return n


def load_confirmations(period: str, conn=None) -> Dict[str, str]:
    """某账期「已确认」的发票号 → 备注。

    优先本模块的 `import_confirm`；该票没有时回退聚合旧维度
    （`merged` 优先，其次 `handler`/`received` 拼接）——与
    `review_compare.load_confirmed_notes` 同口径，保证两个入口看到同一份事实。
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT dim, invoice_no, note FROM anomaly_note WHERE period=?",
            (period,),
        ).fetchall()
    finally:
        if own:
            conn.close()
    primary: Dict[str, str] = {}
    legacy: Dict[str, list] = {}
    for r in rows:
        no = (r["invoice_no"] or "").strip()
        if not no:
            continue
        dim = r["dim"]
        if dim == CONFIRM_DIM:
            primary[no] = r["note"] or ""
        elif dim in _LEGACY_DIMS:
            legacy.setdefault(no, []).append(r["note"] or "")
    out = dict(primary)
    for no, parts in legacy.items():
        if no not in out:
            out[no] = "；".join(p for p in parts if p)
    return out


# ---------------------------------------------------------------------------
# 批 3-2b：导入时就地修改的字段留痕（dim=import_edit）
# 结构与「确认」留痕完全同构：`[{invoice_no, note=字段列表}, ...]`，
# 例如 note="购方、案号"。同票多来源修改（发票行 + 应收账款行）由调用方合并去重。
# 无 legacy 维度 —— 本 dim 是全新引入，不需要回退。
# ---------------------------------------------------------------------------

def clear_edit_hints(conn, period: str) -> int:
    """清掉该账期 `import_edit` 维度的修改留痕；返回删除条数。

    只删自己的 dim —— 确认留痕（`import_confirm`）与旧页人工备注（`merged`）绝不动。
    """
    cur = conn.execute(
        "DELETE FROM anomaly_note WHERE period=? AND dim=?", (period, EDIT_DIM))
    return cur.rowcount or 0


def save_edit_hints(conn, period: str, items: Optional[Iterable[Dict]]) -> int:
    """把导入时就地修改过的行写进 `anomaly_note`（调用方保证同一事务）。

    items: `[{"invoice_no": str, "note": "购方、案号"}, ...]`；票号或字段列表为空的行跳过。
    同票同账期靠主键 `INSERT OR REPLACE` 幂等。返回写入条数。
    """
    n = 0
    for it in items or []:
        no = (it.get("invoice_no") or "").strip()
        note = (it.get("note") or "").strip()
        if not no or not note:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO anomaly_note "
            "(invoice_no, dim, period, note, confirmed_at) "
            "VALUES (?,?,?,?, datetime('now','localtime'))",
            (no, EDIT_DIM, period, note),
        )
        n += 1
    return n


def load_edit_hints(period: str, conn=None) -> Dict[str, str]:
    """某账期「导入时修改过字段」的发票号 → 字段列表（如 `"购方、案号"`）。"""
    own = conn is None
    if own:
        conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT invoice_no, note FROM anomaly_note WHERE period=? AND dim=?",
            (period, EDIT_DIM),
        ).fetchall()
    finally:
        if own:
            conn.close()
    return {(r["invoice_no"] or "").strip(): (r["note"] or "")
            for r in rows if (r["invoice_no"] or "").strip()}
