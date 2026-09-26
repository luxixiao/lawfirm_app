#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""销项导入校验「预检」（只读）：不写库、不改任何文件，只报告每期会触发哪条规则、几行。

用法：
    python scripts/dryrun_invoice_check.py [归档根目录]

输出：每期一行汇总 + 明细（A5 空金额 / A6 空日期 / 解析期拦截 / 跨期红字清单）。
跨期红字还会按改造后的 assert_red_amount_cross_period 判据（**只读**开库副本）算一遍金额。
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.importer.excel_reader import col_index, find_header_row, read_sheet  # noqa: E402
from app.importer.invoice_import import _RED_RE, parse_invoice_file  # noqa: E402

DB = ROOT / "data" / "lawfirm.db"


def scan_file(path: Path, period: str):
    rows = read_sheet(str(path))
    hr = find_header_row(rows, ["发票号码", "价税合计"])
    if hr < 0:
        return {"err": "未找到表头"}
    header = rows[hr]
    i_no = col_index(header, "发票号码")
    i_total = col_index(header, "价税合计")
    i_date = col_index(header, "开票日期")
    i_remark = col_index(header, "备注")

    n = 0
    a5, a6 = [], []
    reds = []
    nos = []
    for idx, r in enumerate(rows[hr + 1:], start=hr + 2):  # 1 基行号
        def g(i: int) -> str:
            return r[i].strip() if 0 <= i < len(r) else ""

        no = g(i_no)
        if not no:
            continue
        n += 1
        nos.append(no)
        if not g(i_total):
            a5.append((idx, no))
        if not g(i_date):
            a6.append((idx, no))
        m = _RED_RE.search(g(i_remark))
        if m:
            try:
                tot = float(g(i_total).replace(",", "")) if g(i_total) else 0.0
            except ValueError:
                tot = 0.0
            if tot < 0:
                reds.append((idx, no, m.group(1), tot))
    return {"n": n, "a5": a5, "a6": a6, "reds": reds, "nos": nos}


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "archive" / "invoice"
    files = sorted(root.rglob("*.xls*"))
    if not files:
        print(f"未找到销项文件: {root}")
        return 1

    # 只读副本，供跨期红字金额判定
    tmpdb = Path(tempfile.mkdtemp(prefix="inv_dryrun_")) / "copy.db"
    if DB.exists():
        shutil.copy2(DB, tmpdb)
        conn = sqlite3.connect(f"file:{tmpdb}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    else:
        conn = None

    print(f"{'期':<10}{'文件':<34}{'张数':>5}{'红字':>5}{'A5空额':>7}{'A6空日':>7}{'解析拦截':>9}{'跨期红字':>9}")
    print("-" * 92)
    detail = []
    for f in files:
        period = f.parent.name
        try:
            s = scan_file(f, period)
        except Exception as e:  # noqa: BLE001
            print(f"{period:<10}{f.name:<34}  扫描异常: {e}")
            continue
        if "err" in s:
            print(f"{period:<10}{f.name:<34}  {s['err']}")
            continue

        # 解析期是否会拦截（A5/A6 必填 + 本月内超额红冲）
        blocked = ""
        try:
            parse_invoice_file(str(f), period)
        except Exception as e:  # noqa: BLE001
            blocked = str(e)

        batch = set(s["nos"])
        cross = [r for r in s["reds"] if r[2] not in batch]
        over = []
        if conn is not None:
            agg: dict[str, float] = {}
            for _idx, _no, orig, tot in cross:
                agg[orig] = agg.get(orig, 0.0) + tot
            for orig, s_amt in agg.items():
                row = conn.execute(
                    "SELECT total_amount FROM invoice WHERE invoice_no=?", (orig,)).fetchone()
                if row is None:
                    continue
                face = float(row["total_amount"] or 0.0)
                marks = ",".join("?" * len(batch))
                lib_red = conn.execute(
                    f"SELECT COALESCE(SUM(total_amount),0) FROM invoice "
                    f"WHERE orig_invoice_no=? AND total_amount<0 "
                    f"AND invoice_no NOT IN ({marks})", (orig, *batch)).fetchone()[0] or 0.0
                if face + s_amt + lib_red < -0.01:
                    over.append((orig, face, s_amt, lib_red))

        print(f"{period:<10}{f.name:<34}{s['n']:>5}{len(s['reds']):>5}"
              f"{len(s['a5']):>7}{len(s['a6']):>7}"
              f"{('是' if blocked else '-'):>9}{len(cross):>9}")

        lines = []
        if blocked:
            lines.append(f"    解析期拦截：{blocked}")
        if s["a5"]:
            lines.append(f"    A5 价税合计为空 {len(s['a5'])} 行（首 5）："
                         + ", ".join(f"行{idx}/{no}" for idx, no in s["a5"][:5]))
        if s["a6"]:
            lines.append(f"    A6 开票日期为空 {len(s['a6'])} 行（首 5）："
                         + ", ".join(f"行{idx}/{no}" for idx, no in s["a6"][:5]))
        if cross:
            lines.append(f"    跨期红字 {len(cross)} 张（原票不在本批）：")
            for idx, no, orig, tot in cross[:10]:
                lines.append(f"      行{idx} {no} {tot:g} → 原票 {orig}")
        if over:
            lines.append("    ❗跨期超额红冲（会被 A9 拦下）：")
            for orig, face, s_amt, lib_red in over:
                lines.append(f"      原票 {orig} 面值{face:g} + 本批{s_amt:g} + 库里{lib_red:g} < 0")
        if lines:
            detail.append(f"  ▸ {period} / {f.name}")
            detail.extend(lines)

    print()
    for d in detail:
        print(d)

    if conn:
        conn.close()
    try:
        tmpdb.unlink()
        tmpdb.parent.rmdir()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
