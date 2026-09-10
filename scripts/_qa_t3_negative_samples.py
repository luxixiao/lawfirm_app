#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA 临时脚本（非交付物）— T3 比对脚本有效性（负样本）验证。

构造 3 个负样本候选目录，分别验证 t3_filediff.py 能真正报错并非 0 退出：
  N1 改坏一个单元格值（应触发 diff_xlsx 第 7 维 cell）
  N2 注入 <pageSetup orientation="portrait">（应触发硬断言 + 第 6 维）
  N3 缺一个文件（应触发集合差异）
另含 N0 = 全量原样复制（正样本，应 PASS / 退出码 0）作为对照组。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile

PY = sys.executable
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GOLDEN = os.path.join(ROOT, "tests", "golden", "person_settlement_exporter")
TMP = os.path.join(ROOT, "scripts", "_qa_t3_tmp")
TARGET = "个人结算总表_丁祥锋.xlsx"


def fresh(d):
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    return d


def run_filediff(cand_dir):
    r = subprocess.run(
        [PY, os.path.join(ROOT, "scripts", "t3_filediff.py"),
         "--golden-dir", GOLDEN, "--candidate-dir", cand_dir],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def copy_golden(dst):
    os.makedirs(dst, exist_ok=True)
    for fn in os.listdir(GOLDEN):
        if fn.lower().endswith(".xlsx"):
            shutil.copy2(os.path.join(GOLDEN, fn), os.path.join(dst, fn))


def corrupt_cell(path):
    """用 openpyxl 改一个明显单元格的值。"""
    import openpyxl
    wb = openpyxl.load_workbook(path)
    ws = wb.worksheets[0]
    ws["B3"] = "QA-CORRUPTED-VALUE"
    wb.save(path)


def inject_page_setup(path):
    """在第一个 worksheet xml 的 </worksheet> 前注入 <pageSetup orientation="portrait"/>。"""
    import zipfile as zf
    tmp = path + ".tmp"
    with zf.ZipFile(path, "r") as zin, zf.ZipFile(tmp, "w", zf.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                txt = data.decode("utf-8")
                txt = txt.replace("</worksheet>",
                                  '<pageSetup orientation="portrait"/></worksheet>')
                data = txt.encode("utf-8")
            zout.writestr(item, data)
    os.replace(tmp, path)


def report(label, rc, out):
    print("=" * 70)
    print(f"[{label}] exit_code = {rc}")
    tail = "\n".join(out.strip().splitlines()[-8:])
    print(tail)
    return rc


def main():
    results = {}

    # N0 positive control: full copy
    d0 = fresh(os.path.join(TMP, "n0_full_copy"))
    copy_golden(d0)
    rc, out = run_filediff(d0)
    results["N0_full_copy"] = rc
    report("N0_full_copy (expect 0)", rc, out)

    # N1 corrupted cell value
    d1 = fresh(os.path.join(TMP, "n1_cell"))
    copy_golden(d1)
    corrupt_cell(os.path.join(d1, TARGET))
    rc, out = run_filediff(d1)
    results["N1_cell"] = rc
    report("N1_cell (expect nonzero)", rc, out)

    # N2 injected pageSetup
    d2 = fresh(os.path.join(TMP, "n2_pagesetup"))
    copy_golden(d2)
    inject_page_setup(os.path.join(d2, TARGET))
    rc, out = run_filediff(d2)
    results["N2_pagesetup"] = rc
    report("N2_pagesetup (expect nonzero)", rc, out)

    # N3 missing file
    d3 = fresh(os.path.join(TMP, "n3_missing"))
    copy_golden(d3)
    os.remove(os.path.join(d3, TARGET))
    rc, out = run_filediff(d3)
    results["N3_missing"] = rc
    report("N3_missing (expect nonzero)", rc, out)

    print("=" * 70)
    print("SUMMARY:", results)
    ok = (results["N0_full_copy"] == 0
          and results["N1_cell"] != 0
          and results["N2_pagesetup"] != 0
          and results["N3_missing"] != 0)
    print("NEGATIVE-SAMPLE SUITE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
