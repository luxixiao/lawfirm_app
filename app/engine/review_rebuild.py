"""导入后读库重建：raw_ledger（+ collection）→ 导入期 data 结构（批 1b）

用途
----
导入复核页的「导入后」模式要与「导入前」共用**同一张表、同一套四态判定**
（`app.ui.unified_import_dialog.UnifiedImportDialog`）。导入前数据来自源文件解析
（`importer.parse_ledger_file`）；导入后源文件可能已不在手边，故改为从该账期
**active 台账批次的 `raw_ledger` 镜像**反向重建，产出与 `parse_ledger_file`
**同构**的 dict，使 `evaluate` / 四态 / 右栏 / 筛选全部原样复用。

口径同源（不重写规则，只调用）
------------------------------
| 维度 | 函数 | 与解析期 |
| --- | --- | --- |
| 日期 | `date_utils.normalize_date` | 同一函数 |
| 经办人 | `parse_handler.parse_handler_column` | 同一函数（纯函数，不查库） |
| 经办人（**库侧真值**） | `charge_detail`（**本台账批次**） | **批 3-2**：原文解析之外再取库值，见下节 |
| 备注 | `parse_remark.parse_remark` | 同一函数 |
| sheet 归属收款修正 | `ledger_import.apply_sheet_receipt_rule` | **同一函数**（批 1b 从内联代码抽出） |
| sheet3 切分 | `ledger_import.split_deferred` | 同一函数 |

### 批 3-2：「经办人分摊」以库侧真值为准（原文解析只作兜底）
`raw_ledger` **刻意**只存台账原文（`importer._insert_raw_ledger` 的明确口径：镜表的
经办人/对方/案号一律取原始行，好让「源 ⇄ 库」逐字对照）⇒ 导入时在复核页手工改过的
分摊，导入后重解析原文只会拿回**旧值**；原文本就解析不出的问题行（人工补的经办人）
更是拿回**空值**，界面上还会重新冒出一堆「无经办人 / 分摊合计≠价税合计」——post 模式
没有「确认」按钮，这些就成了**点不掉的假待办**。真正入库的值在 `charge_detail`。

口径见 `_batch_handlers`：**只认 `import_batch_id = 本台账批次`**（可证明是本次导入
写入的、即人工修正后的值），并要求**与行金额勾稽**才采用。sheet3 行天然不满足
（不建票、不写 `charge_detail`）→ 自动回退原文解析，与「应收账款视角不得混用别的
账期的发票视角」（批 0 §9.6）一致。

> ⚠️ 已知缺口（不属批 3-2）：**sheet3 行在导入时手工修正的分摊，库里没有任何落点**
> （`commit_ledger_import` 对 sheet3 只写镜表），故 post 只能显示台账原文。
> 要回显它得先新增落点（`raw_ledger` 加列 / `anomaly_note` 新 dim），归后续批次。

与导入期**不等价**之处（已知、已接受，见批 0 §9.6）
----------------------------------------------------
1. **`problems` 恒为空**：解析失败的问题行**不写镜表**（`commit_ledger_import` 只镜像
   `invoices` 与 `deferred`）；只有被复核页修正后的行才随 `invoices` 落库。
   故导入后本就不存在「未解析行」——这与「入库 ≡ 全部处理完毕」的硬拦口径一致。
2. **`header` / `raw_row` 恒为空**：`raw_ledger` 是**归一化**镜像（18 列业务字段），
   不是逐列镜像，**不存表头行与原始整行**。
   → 「查看原始台账行」改走批次存档文件（`read_ledger_row`），仍可按原样逐列对照。
3. **显式逐人收款（`split_receipts`）不存镜表**：它来自问题行修正 / 右栏表单编辑，
   镜表里只有备注原文。→ 从 `collection` 表按 `import_batch_id` 读回**入库后的真值**
   （且只在「备注推不出收款」时注入，避免与备注推导口径打架，见 `_collection_splits`）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.db import get_conn
from app.engine.import_confirm import load_confirmations
from app.importer.archive_helper import read_archive_row
from app.importer.date_utils import normalize_date
from app.importer.excel_reader import ImportError_
from app.importer.ledger_import import apply_sheet_receipt_rule, split_deferred
from app.importer.parse_handler import parse_handler_column
from app.importer.parse_remark import parse_remark

# 参与 sheet_totals 归集的 sheet（与 parse_ledger_file 同口径：含 sheet3）
TOTAL_SHEETS = ("sheet1", "sheet2", "sheet3")


def _year_of(period: str) -> Optional[int]:
    """账期 → 年份（日期无年份时的兜底，与解析期 `int(period.split('-')[0])` 同源）。"""
    try:
        return int(str(period).split("-")[0])
    except (ValueError, TypeError, IndexError):
        return None


def _norm_date(raw: Optional[str], year: Optional[int]) -> str:
    """台账原始日期文本 → YYYY-MM-DD；空值/解析失败回空串（**不抛异常**）。

    解析期对失败是「转问题行」；导入后镜表里已是解析通过的行，失败只可能是
    台账被手工改坏 → 回空串，由复核页「查看原始台账行」对照修正。
    """
    if not raw or not str(raw).strip():
        return ""
    try:
        return normalize_date(raw, default_year=year) or ""
    except ImportError_:
        return ""


def active_ledger_batch(period: str, conn=None) -> Optional[Dict]:
    """该账期当前**生效**的发票台账批次（多批取最新）；无则 None。

    `commit_ledger_import` 是「同账期覆盖式导入」：旧批 `status` 被改成非 active，
    故按 `status='active'` 过滤天然只拿到一份（历史批次已被标记 rolled_back）。
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        r = conn.execute(
            "SELECT id, period, file_name, archive_path, imported_at FROM import_batch "
            "WHERE batch_type='ledger' AND period=? AND status='active' "
            "ORDER BY id DESC LIMIT 1",
            (period,),
        ).fetchone()
        return dict(r) if r else None
    finally:
        if own:
            conn.close()


def _collection_splits(conn, batch_id: int) -> Dict[str, list]:
    """本批次写入的收款 → {发票号: [(姓名, 金额, 日期)]}。

    只在「备注推不出收款」时注入（见 `rebuild_period_data`）：普通行的收款由备注推导，
    其 `collection.person_name` 是空串（`_write_collection_for_invoice` 的备注分支不带
    姓名），若盲目注入会把「各经办人已收」列打成 0（显示回退）；
    而问题行修正 / 右栏编辑走 `split_receipts` 分支，**带姓名**且备注已被清空
    —— 这正是镜表丢失、必须从库里补回来的那部分。
    """
    out: Dict[str, list] = {}
    try:
        rows = conn.execute(
            "SELECT invoice_no, person_name, amount, receipt_date FROM collection "
            "WHERE import_batch_id=? ORDER BY id",
            (batch_id,),
        ).fetchall()
    except Exception:  # noqa: BLE001 补不出显式收款不影响主表重建
        return out
    for r in rows:
        no = (r["invoice_no"] or "").strip()
        if not no:
            continue
        out.setdefault(no, []).append(
            ((r["person_name"] or "").strip(), float(r["amount"] or 0.0),
             (r["receipt_date"] or "")[:10]))
    return out


def _batch_handlers(conn, batch_id: int) -> Dict[str, List[Tuple[str, float]]]:
    """本台账批次写入 `charge_detail` 的分摊 → `{发票号: [(姓名, 金额), ...]}`（批 3-2）。

    **为什么需要它**：`raw_ledger` 刻意只存台账**原文**（见
    `importer._insert_raw_ledger` 的口径：经办人/对方/案号一律取原始行，好让
    「源 ⇄ 库」逐字对照）⇒ 导入时在复核页手工改过的分摊，重解析原文只会拿回旧值；
    原文本就解析不出的问题行（人工补的经办人）更会拿回空值。真正入库的值在这里。

    **只认 `import_batch_id = 本批次`**，不是「票号在库就取」：
    - 本批次 ⟺ 本次台账导入把该行当**发票视角**建过账（`commit_ledger_import` 只对
      sheet1/2 建票）⇒ 分摊与行金额必然勾稽（导入前校验过）；
    - 跨批次/跨账期的 `charge_detail` 属**别的账期的发票视角**，而 sheet3 行是
      **应收账款视角**（批 0 §9.6：两者语义不同，不得混用）→ 一律不取。
      sheet3 行因此天然拿不到值、自动回退原文解析。

    同名多行按姓名聚合（与导入侧 `_agg_h` 同口径）——`charge_detail` 本应
    (票号, 姓名) 唯一，这是零成本防御，避免万一出现重复名时被判成「经办人重复」。
    """
    out: Dict[str, List[Tuple[str, float]]] = {}
    try:
        rows = conn.execute(
            "SELECT invoice_no, person_name, billing_amount FROM charge_detail "
            "WHERE import_batch_id=? ORDER BY id",
            (batch_id,),
        ).fetchall()
    except Exception:  # noqa: BLE001 补不出库值不影响主表重建（回退原文解析）
        return out
    agg: Dict[str, Dict[str, float]] = {}
    for r in rows:
        no = (r["invoice_no"] or "").strip()
        name = (r["person_name"] or "").strip()
        if not no or not name:
            continue
        agg.setdefault(no, {})
        agg[no][name] = agg[no].get(name, 0.0) + float(r["billing_amount"] or 0.0)
    for no, by_name in agg.items():
        out[no] = list(by_name.items())
    return out


def _has_remark_receipts(remark: Dict) -> bool:
    """备注侧能否推出收款（纯日期 或 逐期明细）。"""
    return bool(remark.get("pure_date") or remark.get("receipts"))

def rebuild_period_data(period: str, conn=None, in_library: set | None = None) -> Dict:
    """某账期的 `raw_ledger` 镜像 → 与 `parse_ledger_file` 同构的 data。

    返回：`{invoices, deferred, prepayments, sheet_totals, problems, sheet12_total,
    backfills, confirmations, period, batch_id, path, file_name}`。
    - `problems` 恒为 `[]`（问题行不入镜表，见模块 docstring）；
    - `backfills` 恒为 `[]`（补录条目在入库后已写进 `invoice(source='manual')`，
      不再需要随台账流转）；
    - `confirmations`：该账期**导入时点过「确认」**的票号 → 备注（`anomaly_note` 的
      独立 dim，批 3-1），供 post 模式避免重报已处理的疑问；
    - `path` / `file_name` = 该批次存档路径与文件名，供复核页「查看原始台账行」用。

    账期无 active 台账批次（库被清空 / 该期没导过）→ 返回空骨架（各桶为空、批次为空），
    界面因此显示空表而不是报错。
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        out: Dict = {
            "invoices": [], "deferred": [], "prepayments": [],
            "sheet_totals": {}, "problems": [], "sheet12_total": 0.0,
            "backfills": [], "confirmations": {},
            "period": period, "batch_id": None, "path": "", "file_name": "",
        }
        batch = active_ledger_batch(period, conn)
        if batch is None:
            return out
        out["batch_id"] = batch["id"]
        out["path"] = batch.get("archive_path") or ""
        out["file_name"] = batch.get("file_name") or ""
        # 批 3-1：导入时点过「确认」的票号 → 备注。两张疑问（兜底低置信 / 红字⇄蓝字不一致）
        # 只能靠人点确认消掉，镜表里没有任何痕迹；留痕存在 `anomaly_note`（独立 dim）。
        # 复核页 post 模式据此不再把它们重报成「待确认」—— 否则 post 隐藏了「确认」按钮，
        # 会变成用户**点不掉的假待办**。
        out["confirmations"] = load_confirmations(period, conn)

        year = _year_of(period)
        rows = conn.execute(
            "SELECT * FROM raw_ledger WHERE import_batch_id=? "
            "ORDER BY sheet_key, row_no, id",
            (batch["id"],),
        ).fetchall()
        splits = _collection_splits(conn, batch["id"])
        # 批 3-2：本次导入真正入库的分摊（手工修正后的值），供逐行覆盖原文解析结果
        lib_handlers = _batch_handlers(conn, batch["id"])

        for r in rows:
            sheet = (r["sheet_key"] or "").strip()
            name = r["sheet_name"] or ""
            row_no = r["row_no"] or 0
            kind = (r["kind"] or "invoice").strip()
            total = float(r["amount_num"] or 0.0)

            if kind == "prepayment" or sheet == "sheet4":
                out["prepayments"].append({
                    "sheet": "sheet4", "sheet_name": name, "row_no": row_no,
                    "header": [], "raw_row": [],
                    "received_date": _norm_date(r["recv_date_raw"], year) or None,
                    "buyer": r["buyer"] or "",
                    "amount": total,
                    "person_text": r["handler_text"] or "",
                    "remark": r["remark"] or "",
                    "case_no": r["case_no"] or "",
                })
                continue

            no = (r["invoice_no"] or "").strip()
            handler_text = r["handler_text"] or ""
            try:
                handlers = parse_handler_column(handler_text, total, no)
            except ImportError_:
                handlers = []          # 与 deferred 派生同口径：解析失败按空分摊
            # 批 3-2：上面是从**台账原文**解析出的「源侧」值；本次导入真正入库的分摊在
            # `charge_detail`（硬拦保证入库 ≡ 全部处理完，故库侧才是最终真值）。
            # 库侧优先，但带**勾稽门闸**：合计与行金额不符就不采用 —— 宁可照旧显示原文，
            # 也绝不因此冒出「分摊合计≠价税合计」；post 没有「确认」按钮，
            # 这种假疑问就是用户点不掉的假待办（批 1b 的老毛病，不能自己再引入一个）。
            lib_h = lib_handlers.get(no)
            handlers_from_lib = False
            if lib_h and abs(sum(a for _n, a in lib_h) - total) <= 0.01:
                handlers, handlers_from_lib = lib_h, True
            remark_raw = r["remark"] or ""
            try:
                remark = parse_remark(remark_raw, default_year=year)
            except Exception:  # noqa: BLE001 与导入期同：坏备注不阻断重建
                remark = {"receipts": [], "remaining": None, "pure_date": None,
                          "is_red_remark": False, "is_red_off": False}
            apply_sheet_receipt_rule(sheet, remark, total, period)

            item: Dict = {
                "sheet": sheet, "sheet_name": name, "row_no": row_no,
                "header": [], "raw_row": [],
                "invoice_no": no,
                "invoice_date": _norm_date(r["invoice_date_raw"], year),
                "buyer": r["buyer"] or "",
                "total_amount": total,
                "handlers": handlers,
                "handler_text": handler_text,
                # 批 3-2：handlers 是否取自库侧（charge_detail 本批次）。
                # 界面不直接显示，但「源 ⇄ 库」比对与测试要能区分这两个来源。
                "handlers_from_lib": handlers_from_lib,
                "remark_raw": remark_raw,
                "remark": remark,
                "case_no": r["case_no"] or "",
                "is_red": total < 0,
                # 批 3-3：镜表行锚点 + 修订标记 —— post 模式「编辑回写 /
                # 还原为原件」按 raw_id 定位 `apply_edit` / `restore_from_archive`；
                # synced=False（已手工修订）才允许还原。pre 模式解析侧没有这两个键
                # → 编辑入口按「行有没有 raw_id」判定，天然只在 post 出现。
                "raw_id": r["id"],
                "synced": bool(r["synced"]),
            }
            # 显式逐人收款（问题行修正 / 右栏编辑）不存镜表 → 从本批 collection 读回，
            # 且**只在备注推不出收款时**注入（否则会覆盖备注推导的逐人分摊显示）
            if not _has_remark_receipts(remark) and splits.get(no):
                item["split_receipts"] = list(splits[no])
            out["invoices"].append(item)

        for key in TOTAL_SHEETS:
            tot = sum(i["total_amount"] for i in out["invoices"] if i.get("sheet") == key)
            if tot:
                out["sheet_totals"][key] = tot
        out["sheet12_total"] = (out["sheet_totals"].get("sheet1", 0.0)
                                + out["sheet_totals"].get("sheet2", 0.0))
        # sheet3 行统一转入 deferred 并重判 need_backfill（与解析期同一函数，
        # 判定基于**当前**库状态 → 前一账期补录过的票后续账期不再要求重复处理）
        split_deferred(out, period, in_library)
        return out
    finally:
        if own:
            conn.close()


def read_ledger_row(sheet_name: str, row_no: int,
                    archive_path: str) -> Tuple[List[str], List[str]]:
    """从批次存档文件读回原始台账行 → `(header, raw_row)`；读不到返回 `([], [])`。

    导入后模式的「查看原始台账行」用：镜表不存原始行，但存档文件（`import_batch.archive_path`）
    与 `raw_ledger.row_no` / `sheet_name` 足以按同一行号约定读回原文
    （行号口径与 `ledger_import` 一致，见 `archive_helper.read_archive_row`）。
    """
    if not archive_path or row_no <= 0 or not sheet_name:
        return ([], [])
    res = read_archive_row(archive_path, sheet_name, row_no)
    if res is None:
        return ([], [])
    header, raw_row = res
    return (list(header), list(raw_row))
