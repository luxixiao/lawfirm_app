"""sheet3 切分失败可见化（P3-5）单元测试 — offscreen，不写真实库。

验证 `_split_work` 的 except 不再静默：
1. split_deferred 抛异常 → 不向上传播（界面不中断，既有设计）；
2. `dlg._split_failed` 置位；
3. 产生 WARNING 日志（SPLIT 前缀，含账期）；
4. 新数据 load_data 后失败态复位。

运行：python tests/test_split_failure_visible.py
"""
import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

import app.importer.ledger_import as li  # noqa: E402
from app.diag import get_logger  # noqa: E402
from app.ui.unified_import_dialog import UnifiedImportDialog  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


class _LogCatcher(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def main() -> int:
    QApplication.instance() or QApplication([])

    catcher = _LogCatcher()
    log = get_logger()
    log.addHandler(catcher)

    dlg = UnifiedImportDialog()
    try:
        # 拦截 split_deferred → 抛错（_split_work 内是调用期局部 import，须 patch 模块符号）
        orig_split = li.split_deferred

        def boom(d, period):
            raise RuntimeError("探针：切分失败")

        li.split_deferred = boom
        try:
            dlg._split_work({"invoices": []})  # 不应向上抛
            check("切分失败不向上传播", True)
        except Exception as e:  # pragma: no cover
            check("切分失败不向上传播", False, f"raised {e!r}")

        check("_split_failed 已置位", dlg._split_failed is True)
        warns = [r for r in catcher.records
                 if r.levelno >= logging.WARNING and "SPLIT" in r.getMessage()]
        check("产生 WARNING 日志（SPLIT 前缀）", len(warns) >= 1,
              f"records={len(catcher.records)}")
        if warns:
            check("日志含账期", dlg._period in warns[-1].getMessage(),
                  warns[-1].getMessage())

        # 正常切分不再置位（幂等兜底成功路径不误报）
        li.split_deferred = orig_split
        dlg._split_failed = False
        dlg._split_work({"invoices": []})
        check("切分成功不复位为失败", dlg._split_failed is False)

        # 新数据载入后失败态复位
        dlg._split_failed = True
        dlg.load_data({"invoices": [], "problems": [], "deferred": []},
                      period="2025-01")
        check("load_data 复位失败态", dlg._split_failed is False)
    finally:
        li.split_deferred = orig_split
        log.removeHandler(catcher)
        dlg.deleteLater()

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
