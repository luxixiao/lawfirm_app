#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t2_diff.py — T2 回归比对：定位 Python golden（文件名含中文）并与 C# 产物逐格 diff。

为何单独存在
------------
golden 文件名含中文（`{year}年{month}月结算表.xlsx`），而 Windows `.bat` 必须保持纯 ASCII
——cmd.exe 会把 UTF-8 中文按 GBK 解析，导致文件名拼错甚至满屏"不是内部或外部命令"。
因此把「含中文路径的拼接与定位 + 调用 diff_xlsx.py」放到 Python 里（UTF-8 安全），
`.bat` 只负责纯 ASCII 的编排与调用。

用法（仓库根 lawfirm_app/ 下）
------------------------------
    python scripts\\t2_diff.py --year 2026 --month 12 ^
        --candidate csharp\\out\\settlement_report_csharp.xlsx --verbose

退出码（与 scripts/diff_xlsx.py 对齐，另加 3）
--------------------------------------------
    0 = 全绿（7 个维度全部一致）
    1 = 发现差异
    2 = 无法读取工作簿 / 无法导入 diff_xlsx
    3 = golden 或 C# 产物缺失（路径或年份/月份不匹配）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
for _p in (str(ROOT), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def golden_path(out_dir: Path, year: int, month: int) -> Path:
    """golden 路径：与 dump_settlement_report 的命名严格一致（含中文，UTF-8 安全）。"""
    return out_dir / "settlement_report_exporter" / f"{year}年{month}月结算表.xlsx"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="定位 golden（中文名，UTF-8 安全）并与 C# 产物逐格比对",
    )
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--month", type=int, required=True)
    ap.add_argument("--candidate", required=True, help="C# 产出的 xlsx 路径")
    ap.add_argument("--out-dir", default="tests/golden", help="golden 根目录（默认 tests/golden）")
    ap.add_argument("--verbose", action="store_true", help="逐条打印每处不一致详情")
    ap.add_argument("--json", dest="json_out", default=None, help="结构化 diff 写入该 JSON 文件")
    ap.add_argument("--tol", type=float, default=1e-6, help="数值比较容差（默认 1e-6）")
    args = ap.parse_args(argv)

    g = golden_path(Path(args.out_dir), args.year, args.month)
    c = Path(args.candidate)

    if not g.exists():
        print(f"[ERROR] golden 不存在: {g}", file=sys.stderr)
        print("[HINT ] 先运行 dump_golden.py 生成 golden，或确认 --year/--month 与生成时一致。",
              file=sys.stderr)
        return 3
    if not c.exists():
        print(f"[ERROR] C# 产物不存在: {c}", file=sys.stderr)
        print("[HINT ] 先跑 LawFirm.Cli 导出，或检查 --candidate 路径。", file=sys.stderr)
        return 3

    try:
        from diff_xlsx import main as diff_main
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 无法导入 diff_xlsx: {exc}", file=sys.stderr)
        return 2

    argv2 = [str(g), str(c)]
    if args.verbose:
        argv2.append("--verbose")
    if args.json_out:
        argv2 += ["--json", args.json_out]
    argv2 += ["--tol", str(args.tol)]

    print(f"[INFO ] golden   : {g}")
    print(f"[INFO ] candidate: {c}")
    print("-" * 70)
    return diff_main(argv2)


if __name__ == "__main__":
    raise SystemExit(main())
