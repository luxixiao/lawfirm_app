#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""唯一真源 extract_orig_no 的单元测试（批3a：正则合一）。

原 `_RED_RE` 只认销项写法（被红冲蓝字数电票号码：xxx），对发票台账的
「冲<日期>发票<号>」写法命中 0（实测 12 期台账 68 张红字全漏）。
本测试守两条写法都能正确提取，且是唯一真源（无第二份正则）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.importer.parse_remark import extract_orig_no  # noqa: E402

OK, FAILS = 0, []


def check(label, got, want):
    global OK
    if got == want:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} got={got!r} want={want!r}")
        print(f"[FAIL] {label} got={got!r} want={want!r}")


def main() -> int:
    # 销项官方写法
    check("销项写法", extract_orig_no("被红冲蓝字数电票号码：24332000000398937755 红字发票信息确认单编号：X"),
          "24332000000398937755")
    check("销项写法-全角冒号", extract_orig_no("被红冲蓝字数电票号码：24332000000398937755"),
          "24332000000398937755")

    # 发票台账写法（冲<日期>发票<号>）
    check("台账写法", extract_orig_no("冲24.11.4发票24332000000398937755"),
          "24332000000398937755")
    check("台账写法-跨年", extract_orig_no("冲21.10.15发票88929152"),
          "88929152")
    check("台账写法-带后缀", extract_orig_no("冲25.1.2发票24332000000000836840 作废"),
          "24332000000000836840")

    # 空 / 无
    check("空备注", extract_orig_no(""), "")
    check("None", extract_orig_no(None), "")
    check("普通收款备注不误抓", extract_orig_no("25.1.2收1000"), "")
    check("被红冲标记无号", extract_orig_no("25.1.16冲掉"), "")
    check("多笔收款", extract_orig_no("25.10.24收3000,10.31收2000"), "")

    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
