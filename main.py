"""应用入口"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.db import init_db  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    init_db()
    app = QApplication(sys.argv)
    app.setApplicationName("律所开票收款统计")
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
