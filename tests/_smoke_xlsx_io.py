"""xlsx_io 封装冒烟：载入'无默认样式'源台账时屏蔽良性 UserWarning。

源台账（开票台账等财务软件导出）常缺默认样式，openpyxl 会打一条
'Workbook contains no default style' 警告（数据读取不受影响）。
本测试扫描归档里的开票台账，确认：
  - 裸 openpyxl.load_workbook 会触发该警告
  - xlsx_io.load_workbook 不触发该警告，且数据正常读出
归档无样例文件时整测试跳过（不失败）。
"""
from __future__ import annotations

import glob
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl  # noqa: E402
from app.importer.xlsx_io import load_workbook  # noqa: E402


def _count_no_default_style(files):
    for f in files:
        with warnings.catch_warnings(record=True) as rec:
            warnings.simplefilter("always")
            try:
                wb = openpyxl.load_workbook(f, data_only=True)
                wb.close()
            except Exception:
                continue
            if any("no default style" in str(w.message) for w in rec):
                return f
    return None


def main() -> int:
    files = sorted(glob.glob("data/archive/invoice/**/*.xlsx", recursive=True))
    offender = _count_no_default_style(files)
    if not offender:
        print("[SKIP] 归档中未找到触发警告的样例文件，跳过（不失败）")
        return 0

    print(f"[fixture] 触发警告的样例: {offender}")

    # 裸 openpyxl 应触发
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        wb = openpyxl.load_workbook(offender, data_only=True)
        wb.close()
    bare = sum("no default style" in str(w.message) for w in rec)
    print(f"[check] 裸 openpyxl 警告条数 = {bare} (期望 >=1)")
    assert bare >= 1, "裸 openpyxl 未触发预期警告，fixture 可能已变化"

    # 封装应静默
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        wb = load_workbook(offender, data_only=True)
        wb.close()
    wrapped = sum("no default style" in str(w.message) for w in rec)
    print(f"[check] 封装后警告条数 = {wrapped} (期望 0)")
    assert wrapped == 0, "封装后仍打出 'no default style' 警告"

    # 数据正常
    ws = load_workbook(offender)
    rows = ws.worksheets[0].max_row
    print(f"[check] 封装读表 OK, 首 sheet 行数 = {rows} (期望 >0)")
    assert rows > 0

    print("\n[ALL PASS]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
