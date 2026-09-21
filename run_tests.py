#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全量回归入口：逐个跑 tests/test_*.py，并核对 EXPECTED_FILES 是否齐全。

约定（见各批次实施计划 §7）：每新增/扩展一个测试文件，必须同步加进 EXPECTED_FILES，
防止「只写了测试却没登记」导致回归被漏跑。

运行：
    python run_tests.py
退出码：0 = 全绿；非 0 = 有测试失败 或 EXPECTED_FILES 与实际文件不一致。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"

# 全部直接可跑的单元测试（不含 _smoke_* 冒烟脚本）。新增测试必须登记在此。
EXPECTED_FILES = [
    "tests/test_account_subject.py",
    "tests/test_backfill_pending.py",
    "tests/test_book_balance.py",
    "tests/test_calc_data.py",
    "tests/test_calc_eval.py",
    "tests/test_calc_formula.py",
    "tests/test_calc_sheet.py",
    "tests/test_deferred_sheet3.py",
    "tests/test_expense_cat.py",
    "tests/test_expense_public_exclusive.py",
    "tests/test_expense_reimport.py",      # T2 新增（同账期重导字段级 diff）
    "tests/test_expense_validation.py",   # T1 新增
    "tests/test_expense_edit.py",          # T4 新增（详情页保存内核）
    "tests/test_header_norm.py",
    "tests/test_import_confirm.py",
    "tests/test_import_fix_log.py",
    "tests/test_person_settlement.py",     # T1 新增
    "tests/test_raw_ledger_mirror.py",
    "tests/test_red_consistency.py",
    "tests/test_review_compare.py",
    "tests/test_review_writeback.py",
    "tests/test_skin_contract.py",
    "tests/test_staff_type.py",
]


def main() -> int:
    rc = 0

    # 1) EXPECTED_FILES 必须都真实存在
    missing = [f for f in EXPECTED_FILES if not (ROOT / f).exists()]
    if missing:
        rc = 1
        print("[EXPECTED_FILES] 以下登记文件不存在：")
        for f in missing:
            print("  -", f)

    # 2) 实际 tests/test_*.py 必须与 EXPECTED_FILES 一致（防止漏登/多登）
    # Windows 下 relative_to 返回反斜杠路径，与 EXPECTED_FILES 的正斜杠不一致，
    # 需统一为正斜杠再做集合比较，否则会把所有文件误判为「未登记」。
    actual = sorted(str(p.relative_to(ROOT)).replace("\\", "/")
                    for p in TESTS.glob("test_*.py"))
    expected_set = set(EXPECTED_FILES)
    actual_set = set(actual)
    not_registered = sorted(actual_set - expected_set)
    if not_registered:
        rc = 1
        print("[EXPECTED_FILES] 以下测试文件未登记（请加进 EXPECTED_FILES）：")
        for f in not_registered:
            print("  +", f)

    # 3) 逐个运行测试
    print("\n==== 运行测试 ====")
    for rel in EXPECTED_FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        print(f"\n--- {rel} ---")
        try:
            r = subprocess.run([sys.executable, str(path)], cwd=str(ROOT),
                                capture_output=True, text=True)
        except Exception as e:  # noqa: BLE001
            rc = 1
            print(f"  运行异常：{e}")
            continue
        out = (r.stdout or "") + (r.stderr or "")
        # 测试脚本自身会打印 PASS/FAIL；这里透出尾部以便排查
        tail = out.strip().splitlines()[-3:] if out.strip() else []
        for line in tail:
            print("  ", line)
        if r.returncode != 0:
            rc = 1

    print("\n==== 结果 ====")
    if rc == 0:
        print(f"全绿：{len(EXPECTED_FILES)} 个测试文件全部通过")
    else:
        print("存在失败 / 不一致，请查看上方输出")
    return rc


if __name__ == "__main__":
    sys.exit(main())
