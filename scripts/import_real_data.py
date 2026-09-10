#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import_real_data.py — 无头批量导入真实台账（让 T2 回归闸门跑在真实数据上）。

为什么需要
----------
T2 的 diff 闸门要验证结算引擎的**真实数值**，但 data/lawfirm.db 中 charge_detail=0，
导致 build_settlement() 对任何年份恒返回空、golden 永远只是「无数据」占位表
—— 闸门等于空转，只验证了管道、一个数字都没比到。
本脚本用应用自身的导入模块（app/importer/*，已确认无任何 UI 依赖，可无头运行）
把真实台账写进库，让 charge_detail / collection / expense_ledger 有数据。

分派规则（镜像 app/ui/import_view.py 的 guess_type/guess_period）
----------------------------------------------------------------
    职工清单.xlsx            -> staff   （无账期，period="0000"）
    销项导出/*.xlsx          -> invoice -> import_invoice_file(path, period)
    发票台账/*               -> ledger   -> import_ledger_file(path, period, on_confirm=自动确认)
    费用台账/*.xlsx          -> expense -> import_expense_file(path, period)
账期从文件名解析：2025.1 / 2025-01 / 2025年1月 -> "2025-01"。

安全设计
--------
1. 导入前先**备份** data/lawfirm.db -> data/lawfirm.db.bak-<时间戳>；
2. 源文件只被 shutil.copy2 **复制**进 data/archive，绝不移动/删除原件
   （见 app/importer/importer.py:_archive_file）；
3. 同「类型+账期」重复导入是**覆盖式**（_drop_active_batch 先撤销旧 active 批次），
   不会灌重复数据；
4. 需要回退：app.importer.importer.rollback_batch(batch_id)。

用法（仓库根 lawfirm_app/ 下，必须用 venv python，因为要 openpyxl/xlrd）
------------------------------------------------------------------------
    %USERPROFILE%\\.lawfirm_venv\\Scripts\\python.exe scripts\\import_real_data.py ^
        --dir C:\\path\\to\\数据表格
    :: 只导入某一类（可重复 --only）
    ... --only ledger --only expense
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import DB_PATH, get_conn  # noqa: E402
from app.importer.importer import (  # noqa: E402
    import_expense_file,
    import_invoice_file,
    import_ledger_file,
)

_PERIOD_RE = re.compile(r"(\d{4})\s*[.\-年/]\s*(\d{1,2})")


def guess_period(filename: str) -> str | None:
    """2025.1 / 2025-01 / 2025年1月 -> 2025-01"""
    m = _PERIOD_RE.search(filename)
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"


def classify(path: Path, root: Path) -> str:
    """按所在目录 + 文件名判定类型（镜像 import_view.guess_type 的实际分派效果）。"""
    name = path.name
    parent = path.parent.name
    if "职工" in name:
        return "staff"
    if parent == "销项导出" or "销项" in name:
        return "invoice"
    if parent == "发票台账" or ("台账" in name and "费用" not in name):
        return "ledger"
    if parent == "费用台账" or "费用" in name:
        return "expense"
    return "unknown"


def backup_db() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = DB_PATH.with_name(f"{DB_PATH.name}.bak-{stamp}")
    shutil.copy2(DB_PATH, dest)
    return dest


def import_staff(path: Path) -> str:
    """职工清单：镜像 app/ui/import_view.py 的写库逻辑（按姓名 upsert）。"""
    from app.importer.staff_import import parse_staff_file

    # 注意：parse_staff_file 的第二个返回值**不是错误串**（实际是文件哈希之类的标识），
    # UI 里用 `_` 直接忽略。早期版本误把它当错误 -> 直接 raise，导致职工清单永远导入失败。
    # 这里与 UI 保持一致：忽略第二个返回值，只在列表为空时才报错。
    staff, _ = parse_staff_file(str(path))
    if not staff:
        raise RuntimeError("未解析到任何职工记录")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO import_batch (batch_type, period, file_name, imported_at)"
            " VALUES (?,?,?,?)",
            ("staff", "0000", path.name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        batch_id = cur.lastrowid
        n_new = n_upd = 0
        for name, stype, note in staff:
            stype = stype or "聘用"
            r = conn.execute("SELECT id FROM staff WHERE name=?", (name,)).fetchone()
            if r:
                conn.execute(
                    "UPDATE staff SET staff_type=?, note=?, is_active=1 WHERE id=?",
                    (stype, note, r["id"]),
                )
                n_upd += 1
            else:
                conn.execute(
                    "INSERT INTO staff (name, staff_type, is_active, note, source,"
                    " import_batch_id) VALUES (?,?,1,?,?,?)",
                    (name, stype, note, "import", batch_id),
                )
                n_new += 1
        conn.commit()
    finally:
        conn.close()
    return f"职工清单: 新增 {n_new}，更新 {n_upd}"


def _format_problem_warning(period: str, problems: list) -> str:
    """把「被静默丢弃的问题行」渲染成醒目的多行红字警告。

    为什么：无头批量导入走 on_confirm=自动确认，无法弹交互修正框，problems 只能丢弃。
    丢弃必须可见 —— 否则台账与销项文档的差额要到回归闸门报 diff 时才暴露，
    那时已不知是哪一张票、哪一行、什么原因。
    """
    if not problems:
        return ""
    lines = [
        "",
        "        " + "!" * 66,
        f"        !!! [WARN] 发票台账 {period}：有 {len(problems)} 行解析失败，已被丢弃（未入库）!!!",
        "        " + "!" * 66,
    ]
    for i, pr in enumerate(problems, 1):
        sheet = pr.get("sheet") or pr.get("sheet_name") or "?"
        lines.append(
            f"        {i:>3}. [{sheet} 第{pr.get('row_no', '?')}行]"
            f" 发票号={pr.get('invoice_no') or '-'}"
            f" 金额={pr.get('total_amount') or '-'}"
            f" 经办人={pr.get('handler_text') or '-'}"
        )
        lines.append(f"             原因: {pr.get('reason') or '未说明'}")
    lines.append("        → 影响：这些行不会入库，台账合计会小于销项文档，回归闸门会报 diff。")
    lines.append("        → 处理：在台账原件修正后重导；或改用带交互修正框的 UI 导入。")
    lines.append("        " + "!" * 66)
    return "\n".join(lines)


def do_import(path: Path, kind: str, period: str) -> str:
    if kind == "staff":
        return import_staff(path)
    p = str(path)
    if kind == "invoice":
        r = import_invoice_file(p, period)
        return f"销项 {period}: {r.get('count')} 张发票"
    if kind == "ledger":
        # on_confirm 直接回传 data = 自动确认（无交互）；commit_ledger_import
        # 内部会再跑一次 validate_ledger_before_write 兜底，有问题会抛异常。
        #
        # ⚠️ 这里**故意**用无交互自动确认（无头批量导入无法弹修正框），代价是
        #    data["problems"] 里的问题行会被静默丢弃 —— 曾因此丢掉一张 72,457.48 的
        #    发票（2025.9 台账，经办人「管理人报酬（道兴家私）」解析失败），导致
        #    sheet1 合计与销项文档对不上，排查花了两轮。教训：**静默丢数据不可接受**。
        #    所以下面在确认前把问题行全部打印出来（红字 + 明细），有就吵醒用户。
        problems: list[dict] = []

        def _auto_confirm(data, validator):
            problems.extend(data.get("problems") or [])
            return data

        r = import_ledger_file(p, period, on_confirm=_auto_confirm)
        cnt = r.get("count") if isinstance(r, dict) else None
        msg = f"发票台账 {period}: {cnt if cnt is not None else r}"
        if problems:
            msg += _format_problem_warning(period, problems)
        return msg
    if kind == "expense":
        r = import_expense_file(p, period)
        return f"费用台账 {period}: {r.get('count')} 条"
    raise RuntimeError(f"未知类型: {kind}")


def main() -> int:
    ap = argparse.ArgumentParser(description="无头批量导入真实台账")
    ap.add_argument("--dir", required=True, help="台账根目录（含 发票台账/ 费用台账/ 销项导出/ 职工清单.xlsx）")
    ap.add_argument("--only", action="append", default=None,
                    choices=["staff", "invoice", "ledger", "expense"],
                    help="只导入指定类型，可重复")
    ap.add_argument("--no-backup", action="store_true", help="跳过备份（不推荐）")
    args = ap.parse_args()

    root = Path(args.dir)
    if not root.is_dir():
        print(f"[ERROR] 目录不存在: {root}", file=sys.stderr)
        return 2

    if not args.no_backup:
        bak = backup_db()
        print(f"[BACKUP] 已备份数据库 -> {bak}")

    # 收集文件
    items = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith("~$"):
            continue
        if p.suffix.lower() not in (".xls", ".xlsx", ".xlsm"):
            continue
        kind = classify(p, root)
        period = guess_period(p.name)
        if kind == "unknown":
            print(f"[SKIP ] 无法识别类型: {p.relative_to(root)}")
            continue
        if kind != "staff" and not period:
            print(f"[SKIP ] 无法从文件名解析账期: {p.relative_to(root)}")
            continue
        if args.only and kind not in args.only:
            continue
        items.append((kind, period or "0000", p))

    # 顺序：staff -> invoice(销项) -> ledger(发票台账) -> expense(费用台账)
    order = {"staff": 0, "invoice": 1, "ledger": 2, "expense": 3}
    items.sort(key=lambda t: (order[t[0]], t[1]))

    print(f"[INFO ] 待导入 {len(items)} 个文件，数据库: {DB_PATH}")
    print("-" * 70)
    ok = fail = 0
    warned = 0
    failed_files = []
    warned_files = []
    for kind, period, p in items:
        rel = p.relative_to(root)
        try:
            msg = do_import(p, kind, period)
            if "[WARN]" in msg:
                # 文件本身导入成功，但有行被丢弃 —— 既不算 OK 也不算 FAIL，单列
                print(f"[WARN ] {rel}\n         {msg}")
                warned += 1
                warned_files.append(str(rel))
            else:
                print(f"[  OK ] {rel}\n         {msg}")
                ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL ] {rel}\n         {type(exc).__name__}: {exc}")
            if os.environ.get("IMPORT_TRACE"):
                traceback.print_exc()
            fail += 1
            failed_files.append((str(rel), str(exc)))

    print("-" * 70)
    print(f"[DONE ] 成功 {ok}，失败 {fail}，有行被丢弃 {warned}")
    if warned_files:
        print("[WARN ] 以下文件有行解析失败被丢弃，请务必核对（差额会传递到回归闸门）:")
        for f in warned_files:
            print(f"        - {f}")
    if failed_files:
        print("[HINT ] 设环境变量 IMPORT_TRACE=1 可打印完整堆栈")
        for f, e in failed_files:
            print(f"        - {f}: {e}")

    # 关键计数
    conn = get_conn()
    try:
        print("-" * 70)
        for t in ("invoice", "charge_detail", "collection", "expense_ledger", "staff", "refund"):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"        {t:<16} {n}")
            except Exception as exc:  # noqa: BLE001
                print(f"        {t:<16} ERR {exc}")
    finally:
        conn.close()

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
