#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""self_diff_golden.py — 自检 golden xlsx：每个文件与自己逐格 diff，期望 0 差异。

为什么需要它（而不是在 bat 里 `for /r` 调 diff_xlsx.py）：
  Windows cmd.exe 的 `for /r` 会把含中文的文件名（如 `2026年度开票收入.xlsx`）
  经当前代码页（GBK/936）传给 python.exe，python 拿到的 argv 路径被错误解码，
  openpyxl 找不到文件 -> 退出码 2 -> bat 误报 self-diff FAILED。
  本脚本在 Python 内部用 `Path.rglob` 列举（走 Windows Unicode API，不会乱码），
  因此 cmd 完全不传递任何中文参数，从根上避开该坑。

本脚本会把完整报告（含每文件 [OK]/[FAIL] 与任何异常 traceback）写入
scripts/self_diff_report.txt，方便在 bat 窗口关闭/滚屏后仍能拿到真实失败原因。

用法（bat 内）：
    python scripts/self_diff_golden.py
退出码：0 = 全部通过；1 = 存在任意失败；2 = 未找到 golden xlsx 或发生异常。
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

# 防止在 GBK/非 UTF-8 控制台打印中文文件名时抛 UnicodeEncodeError 导致脚本崩溃
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import diff_xlsx as dx  # noqa: E402

REPORT = HERE / "self_diff_report.txt"


def _write_report(text: str) -> None:
    try:
        REPORT.write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    buffer: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        buffer.append(msg)

    golden_dir = ROOT / "tests" / "golden"
    files = sorted(golden_dir.rglob("*.xlsx"))
    if not files:
        log(f"[WARN] tests/golden 下未发现任何 .xlsx（golden 可能尚未生成）: {golden_dir}")
        _write_report("\n".join(buffer))
        return 2

    log(f"[INFO] 发现 {len(files)} 个 golden xlsx，开始逐文件 self-diff ...")
    failures = 0
    for f in files:
        rel = f.relative_to(ROOT)
        try:
            base_wb = dx.load_workbook(str(f))
            cand_wb = dx.load_workbook(str(f))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            log(f"[ERROR] 无法读取 {rel}: {exc}")
            continue

        rep = dx.DiffReport(baseline=str(f), candidate=str(f), tol=1e-6)
        dx.compare_workbooks(base_wb, cand_wb, 1e-6, rep)
        if rep.passed:
            log(f"[OK]   self-diff passed: {rel}")
        else:
            failures += 1
            log(f"[FAIL] self-diff FAILED on: {rel}")
            for d in rep.diffs:
                log(f"  [{d.category}] {d.sheet} {d.location}: {dx._fmt(d.expected)} -> {dx._fmt(d.actual)}")

    log("-" * 70)
    passed = len(files) - failures
    log(f"[RESULT] self-diff: {passed}/{len(files)} 通过, {failures} 失败")
    _write_report("\n".join(buffer))
    return 1 if failures else 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        print(tb)
        _write_report("EXCEPTION TRACEBACK:\n" + tb)
        rc = 2
    raise SystemExit(rc)
