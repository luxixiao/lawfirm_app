"""应用入口"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402

from app import __version__  # noqa: E402
from app.db import init_db, run_received_snapshot_backfill  # noqa: E402
from app.ui import i18n, style  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.diag import (  # noqa: E402
    setup_logging, log_boot, install_window_filter, get_logger, make_app,
)


def _is_already_running() -> bool:
    """Windows 命名互斥锁检测：已有实例在运行则返回 True。"""
    kernel32 = ctypes.windll.kernel32
    # 64 位句柄需要用 c_void_p 接收，避免截断
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel32.CreateMutexW(None, False, "Global\\lawfirm_app_single_instance")
    if not mutex:
        return False
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def _show_running_alert() -> None:
    ctypes.windll.user32.MessageBoxW(
        0,
        "律所开票收款统计 已经在运行，请勿重复启动。",
        "已在运行",
        0x00000040,  # MB_ICONINFORMATION
    )


def resource_path(rel: str) -> str:
    # 打包后资源随 exe 解压到 sys._MEIPASS；开发期用项目根目录
    base = getattr(sys, "_MEIPASS", str(Path(__file__).resolve().parent))
    return os.path.join(base, rel)


def main() -> int:
    # 诊断日志（排查导入弹窗用）：在任何可能提前 return 之前先落盘启动环境信息
    try:
        setup_logging()
        log_boot()
    except Exception:  # noqa: BLE001 - 日志初始化失败绝不影响启动
        pass

    # 先做单实例检测，避免双击 run_app.bat 产生两个主窗口
    if _is_already_running():
        get_logger().info("BOOT  already running -> exit")
        _show_running_alert()
        return 0

    # backfill=False：收款认定快照补齐延后到首屏渲染之后，避免阻塞双击启动
    init_db(backfill=False)
    app = make_app(sys.argv)
    app.setWindowIcon(QIcon(resource_path("app.ico")))
    app.setApplicationName("律所开票收款统计")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    # Qt 内置文案中文化：QMessageBox / QDialogButtonBox 的标准按钮（OK/Cancel/Yes/No/
    # Close/Show Details...）与 QFileDialog 都靠这个翻译表，否则一律英文。
    # 必须在任何窗口/弹窗创建之前装载。
    i18n.install_qt_zh(app)
    style.apply_skin(app, style.load_skin_pref())
    try:
        install_window_filter(app)
    except Exception:  # noqa: BLE001
        pass
    try:
        get_logger().info("BOOT  qt_i18n=%s", i18n.installed() or "none(英文回退)")
    except Exception:  # noqa: BLE001
        pass
    win = MainWindow()
    win.show()
    # 首屏渲染后再补齐收款认定快照（避免阻塞启动；幂等，失败仅告警）
    QTimer.singleShot(200, run_received_snapshot_backfill)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
