"""应用入口"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402

from app.db import init_db, run_received_snapshot_backfill  # noqa: E402
from app.ui import style  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    # backfill=False：收款认定快照补齐延后到首屏渲染之后，避免阻塞双击启动
    init_db(backfill=False)
    app = QApplication(sys.argv)
    app.setApplicationName("律所开票收款统计")
    app.setStyle("Fusion")
    style.apply_skin(app, style.load_skin_pref())
    win = MainWindow()
    win.show()
    # 首屏渲染后再补齐收款认定快照（避免阻塞启动；幂等，失败仅告警）
    QTimer.singleShot(200, run_received_snapshot_backfill)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
