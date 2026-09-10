#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA 临时脚本（非交付物）— T3 golden 页面设置扫描。

目的：独立复核规格书 §6.4 声称的「golden 全部 80 个 sheet 无 <pageSetup>，
margin 恒为 0.75/0.75/1.0/1.0」。用两种独立手段：
  1) openpyxl 读 page_setup / page_margins（与 diff_xlsx.py 同款取值路径）
  2) zipfile 直读 XML 正则 <pageSetup（与 t3_filediff.py 同款手段）
两法互相印证。
"""
from __future__ import annotations

import glob
import os
import re
import sys
import zipfile

import openpyxl

GOLDEN = os.path.join("tests", "golden", "person_settlement_exporter")
_SHEET_XML_RE = re.compile(r"^xl/worksheets/sheet\d+\.xml$")
_PAGE_SETUP_RE = re.compile(rb"<pageSetup\b")


def xml_page_setup(path):
    hits = []
    with zipfile.ZipFile(path, "r") as zf:
        for name in zf.namelist():
            if _SHEET_XML_RE.match(name) and _PAGE_SETUP_RE.search(zf.read(name)):
                hits.append(name)
    return hits


def main():
    files = sorted(glob.glob(os.path.join(GOLDEN, "*.xlsx")))
    print(f"golden files: {len(files)}")
    total_sheets = 0
    page_setup_hits = []       # (file, sheet) 含 <pageSetup>
    margin_bad = []            # (file, sheet, side, value)
    orient_non_none = []       # (file, sheet, orientation)
    fit_non_default = []
    seen_margins = set()

    for fp in files:
        base = os.path.basename(fp)
        # --- 手段 2：直读 XML ---
        xml_hits = xml_page_setup(fp)
        # --- 手段 1：openpyxl ---
        wb = openpyxl.load_workbook(fp, data_only=False, read_only=False)
        for ws in wb.worksheets:
            total_sheets += 1
            ps = ws.page_setup
            if ps.orientation is not None:
                orient_non_none.append((base, ws.title, ps.orientation))
            if ps.fitToWidth is not None or ps.fitToHeight is not None:
                fit_non_default.append((base, ws.title, ps.fitToWidth, ps.fitToHeight))
            pm = ws.page_margins
            expect = {"left": 0.75, "right": 0.75, "top": 1.0, "bottom": 1.0}
            got = {s: getattr(pm, s, None) for s in expect}
            seen_margins.add(tuple(sorted(got.items())))
            for side, exp in expect.items():
                v = got[side]
                if v is None or abs(float(v) - exp) > 1e-9:
                    margin_bad.append((base, ws.title, side, v, exp))
        if xml_hits:
            for h in xml_hits:
                page_setup_hits.append((base, h))

    print(f"total sheets (openpyxl): {total_sheets}")
    print(f"distinct margin tuples: {sorted(seen_margins)}")
    print(f"sheets with orientation != None: {len(orient_non_none)}")
    print(f"sheets with fitToWidth/Height set: {len(fit_non_default)}")
    print(f"sheets with <pageSetup> in XML: {len(page_setup_hits)}")
    for h in page_setup_hits[:20]:
        print("   PAGESETUP_HIT:", h)
    print(f"margin violations: {len(margin_bad)}")
    for m in margin_bad[:20]:
        print("   MARGIN_BAD:", m)
    for o in orient_non_none[:20]:
        print("   ORIENT:", o)
    for f in fit_non_default[:20]:
        print("   FIT:", f)

    ok = (not page_setup_hits) and (not margin_bad) and (not orient_non_none)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
