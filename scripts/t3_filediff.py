#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t3_filediff.py — T3 目录级配对批量 diff（个人结算总表）。

为何单独存在
------------
golden `tests/golden/person_settlement_exporter/` 是「每人一个文件」（共 42 个 xlsx），
C# 产物目录同理。`diff_xlsx.py` 只做单文件对单文件，故这里加一层「目录配对 + 批量调用」
编排，**不修改 diff_xlsx.py 本体**（它是被验证的可信基准工具）。

额外硬断言（规格书 §6.2 第 6 条）
--------------------------------
直读 C# 产物每个 sheet 的 XML，断言**不含 `<pageSetup>` 元素**。
原因：golden 全部 sheet 无 `<pageSetup>`（openpyxl 读回 orientation=None），而
`diff_xlsx.py._compare_page_setup` 对 orientation **不做归一化**；一旦 C# 侧误调
`PrintSetup.Landscape` 就会落盘 `orientation="portrait"` → 全红。该坑最隐蔽，故
在此独立提前暴露（zipfile + 正则，无需 openpyxl）。

用法（仓库根 lawfirm_app/ 下）
------------------------------
    python scripts\\t3_filediff.py ^
        --year 2026 --candidate-dir csharp\\out\\person_settlement --verbose

退出码
------
    0 = 全绿（文件名集合一致 + 每文件 7 维一致 + 无 <pageSetup>）
    1 = 发现差异
    2 = 无法读取工作簿 / 无法导入 diff_xlsx
    3 = golden 或 candidate 目录缺失
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
for _p in (str(ROOT), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_PAGE_SETUP_RE = re.compile(rb"<pageSetup\b")
# worksheet XML 路径：xl/worksheets/sheetN.xml（排除 drawing / 其它）
_SHEET_XML_RE = re.compile(r"^xl/worksheets/sheet\d+\.xml$")


def _xlsx_names(directory: Path) -> set:
    """目录下所有 *.xlsx 的文件名集合（不含路径）。"""
    if not directory.is_dir():
        return set()
    return {p.name for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".xlsx"}


def _assert_no_page_setup(xlsx_path: Path) -> list:
    """直读 xlsx 的 XML，返回含 <pageSetup> 的 sheet 名列表（空 = 通过）。

    用 zipfile 读 xl/worksheets/*.xml，正则匹配 `<pageSetup`（无需 openpyxl）。
    返回的是「内部 sheetN.xml 文件名」列表，便于定位。
    """
    offenders = []
    try:
        with zipfile.ZipFile(xlsx_path, "r") as zf:
            for name in zf.namelist():
                if not _SHEET_XML_RE.match(name):
                    continue
                data = zf.read(name)
                if _PAGE_SETUP_RE.search(data):
                    offenders.append(name)
    except Exception as exc:  # noqa: BLE001
        # 读失败也视为异常，返回特殊标记让上层报错
        offenders.append(f"<read-error: {exc!r}>")
    return offenders


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="T3 目录级配对批量 diff + 无 pageSetup 硬断言",
    )
    ap.add_argument("--year", type=int, default=2026, help="数据年份（仅用于信息展示）")
    ap.add_argument("--golden-dir", default="tests/golden/person_settlement_exporter",
                    help="golden 目录（默认 tests/golden/person_settlement_exporter）")
    ap.add_argument("--candidate-dir", default="csharp/out/person_settlement",
                    help="C# 产物目录（默认 csharp/out/person_settlement）")
    ap.add_argument("--verbose", action="store_true", help="逐条打印每处不一致详情")
    ap.add_argument("--json", dest="json_out", default=None, help="目录级汇总 JSON 输出路径")
    ap.add_argument("--tol", type=float, default=1e-6, help="数值比较容差（默认 1e-6）")
    args = ap.parse_args(argv)

    golden_dir = Path(args.golden_dir)
    cand_dir = Path(args.candidate_dir)

    if not golden_dir.is_dir():
        print(f"[ERROR] golden 目录不存在: {golden_dir}", file=sys.stderr)
        print("[HINT ] 先运行 scripts\\t3_verify.bat，或确认 --golden-dir。", file=sys.stderr)
        return 3
    if not cand_dir.is_dir():
        print(f"[ERROR] C# 产物目录不存在: {cand_dir}", file=sys.stderr)
        print("[HINT ] 先运行 LawFirm.Cli --report person，或确认 --candidate-dir。", file=sys.stderr)
        return 3

    try:
        from diff_xlsx import main as diff_main
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 无法导入 diff_xlsx: {exc}", file=sys.stderr)
        return 2

    golden_names = _xlsx_names(golden_dir)
    cand_names = _xlsx_names(cand_dir)

    print("=" * 70)
    print("T3 FILEDIFF REPORT (person_settlement_exporter)")
    print(f"year      : {args.year}")
    print(f"golden    : {golden_dir}  ({len(golden_names)} files)")
    print(f"candidate : {cand_dir}  ({len(cand_names)} files)")
    print(f"tolerance : {args.tol}")
    print("=" * 70)

    summary = {
        "year": args.year,
        "golden_dir": str(golden_dir),
        "candidate_dir": str(cand_dir),
        "golden_count": len(golden_names),
        "candidate_count": len(cand_names),
        "missing_in_candidate": [],
        "extra_in_candidate": [],
        "file_results": {},
        "page_setup_offenders": {},
    }

    total_diffs = 0

    # ---- 集合比对：golden 有但 candidate 缺 / candidate 多出 ----
    missing = sorted(golden_names - cand_names)
    extra = sorted(cand_names - golden_names)
    for n in missing:
        print(f"[FAIL] {n}  <= golden 存在但 candidate 缺失")
        total_diffs += 1
    for n in extra:
        print(f"[FAIL] {n}  <= candidate 多出 golden 没有的文件")
        total_diffs += 1
    summary["missing_in_candidate"] = missing
    summary["extra_in_candidate"] = extra

    # ---- 逐同名文件：pageSetup 硬断言 + diff_xlsx 7 维 ----
    for name in sorted(golden_names & cand_names):
        g = golden_dir / name
        c = cand_dir / name

        # 硬断言：C# 产物不含 <pageSetup>
        offenders = _assert_no_page_setup(c)
        if offenders:
            print(f"[FAIL] {name}  <= C# 产物含 <pageSetup>（规格书 §6.2 第 6 条）：{offenders}")
            total_diffs += len(offenders)
            summary["page_setup_offenders"][name] = offenders

        argv2 = [str(g), str(c)]
        if args.verbose:
            argv2.append("--verbose")
        argv2 += ["--tol", str(args.tol)]
        print("-" * 70)
        print(f"[FILE] {name}")
        rc = diff_main(argv2)
        summary["file_results"][name] = rc
        if rc != 0:
            total_diffs += 1

    print("=" * 70)
    if total_diffs == 0:
        print(f"[PASS] all {len(golden_names)} files match golden (7 dims + no <pageSetup>)")
    else:
        print(f"[FAIL] total issues: {total_diffs}")
    print("=" * 70)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] 目录级汇总已写入: {out}")

    return 0 if total_diffs == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
