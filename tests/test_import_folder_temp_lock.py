#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量导入文件夹扫描：跳过 Excel/LibreOffice 临时锁文件（~$ / .~lock.）。

回归：批量导入按「同账期同类型」分组检测重复文件时，Excel 打开文件生成的
隐藏锁文件 ~$销项导出2025.05.xlsx 会被 glob('*.xls*') 一并扫到，若无过滤会被
误判为「同账期同类型第二份」而中止整批导入（见 2025.05 批量导入误报重复）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ui.import_view import (  # noqa: E402
    _is_temp_lock_file, _batch_period_type_conflicts,
)

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
    # ---- _is_temp_lock_file ----
    check("Excel锁文件~$销项", _is_temp_lock_file("~$销项导出2025.05.xlsx"), True)
    check("Excel锁文件~$台账", _is_temp_lock_file("~$2025.1台账.xlsx"), True)
    check("LibreOffice锁文件", _is_temp_lock_file(".~lock.foo.xlsx"), True)
    check("正常销项文件", _is_temp_lock_file("销项导出2025.05.xlsx"), False)
    check("正常台账文件", _is_temp_lock_file("2025.1台账.xlsx"), False)

    # ---- _batch_period_type_conflicts ----
    # 回归核心：真文件 + 其 Excel 锁文件同账期同类型 → 不应判为冲突
    check("锁文件不误判(单月)",
          _batch_period_type_conflicts(
              ["销项导出2025.05.xlsx", "~$销项导出2025.05.xlsx"]), {})
    check("锁文件不误判(多月)",
          _batch_period_type_conflicts(
              ["销项导出2025.01.xlsx", "~$销项导出2025.01.xlsx",
               "销项导出2025.02.xlsx", "~$销项导出2025.02.xlsx"]), {})
    # 真正的两份不同名同账期同类型 → 仍要判冲突（保护覆盖风险）
    real_dup = _batch_period_type_conflicts(
        ["销项导出2025.05.xlsx", "销项导出2025.05(2).xlsx"])
    check("真实重复仍报冲突", len(real_dup) == 1, True)
    if real_dup:
        vals = list(real_dup.values())[0]
        check("冲突含两份真文件",
              set(vals) == {"销项导出2025.05.xlsx", "销项导出2025.05(2).xlsx"}, True)
        check("冲突不含锁文件", all(not n.startswith("~$") for n in vals), True)
    # 职工清单（无账期语义）+ 其锁文件 → 不冲突
    check("职工清单+锁文件不冲突",
          _batch_period_type_conflicts(["职工清单.xlsx", "~$职工清单.xlsx"]), {})
    # 不同类型同账期 → 不冲突
    check("不同类型同账期不冲突",
          _batch_period_type_conflicts(
              ["销项导出2025.05.xlsx", "2025.05费用.xlsx"]), {})

    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
