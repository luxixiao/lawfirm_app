"""诊断日志（排查「导入后独立弹窗」用）

作用：
- 记录程序启动环境（sys.executable / argv / cwd / frozen），判断跑的是源码还是旧 exe。
- 用 QApplication.notify 覆写，同步捕获**每一个被 show()/exec() 出来的顶层窗口**
  （主窗口、任何独立对话框、任何 setParent(None) 后被显示的临时窗口），记录类名+标题+
  是否 QDialog+其父对象。这比事件过滤器/轮询可靠（前者收不到 Show 事件，后者会漏掉
  紧凑循环里瞬时出现的窗口）。
- 附带一个 250ms 轮询看门狗，记录**累积出现的顶层窗口**（含无标题的 QWidget）。
- 在导入流程关键分支打点。

日志落盘到 data/app_debug.log（追加写入）。调测结束后可删除本文件与埋点。
"""
from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QWidget

_LOGGER_NAME = "lawfirm_diag"
_LOG_PATH: str | None = None
_watchdog_started = False


def _default_log_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))          # .../app
    proj = os.path.dirname(here)                               # .../lawfirm_app
    for cand in (os.path.join(proj, "data"), proj):
        if os.path.isdir(cand):
            return os.path.join(cand, "app_debug.log")
    return os.path.join(proj, "app_debug.log")


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def setup_logging() -> str:
    global _LOG_PATH
    if _LOG_PATH is not None:
        return _LOG_PATH
    path = _default_log_path()
    _LOG_PATH = path

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    for h in list(logger.handlers):
        logger.removeHandler(h)

    try:
        fh = logging.FileHandler(path, encoding="utf-8", delay=True)
    except OSError:
        fh = logging.FileHandler(os.path.join(os.path.dirname(path) or ".", "app_debug.log"),
                                 encoding="utf-8", delay=True)
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return path


def _safe_version() -> str:
    try:
        from app import __version__
        return str(__version__)
    except Exception:
        return "?"


def log_boot() -> None:
    logger = get_logger()
    try:
        import PySide6
        qt_ver = PySide6.__version__
    except Exception:
        qt_ver = "?"
    logger.info("=" * 64)
    logger.info("BOOT  version=%s", _safe_version())
    logger.info("BOOT  sys.executable=%s", sys.executable)
    logger.info("BOOT  sys.argv=%s", sys.argv)
    logger.info("BOOT  cwd=%s", os.getcwd())
    logger.info("BOOT  frozen(exe)=%s", bool(getattr(sys, "frozen", False)))
    logger.info("BOOT  PySide6=%s", qt_ver)
    logger.info("BOOT  log_path=%s", _LOG_PATH)
    logger.info("=" * 64)


def _describe(recv) -> str:
    """描述一个被显示的窗口。"""
    try:
        title = recv.windowTitle()
    except Exception:
        title = "?"
    parent = None
    try:
        p = recv.parent()
        if p is not None:
            parent = type(p).__name__
    except Exception:
        pass
    return "class=%s title=%r dialog=%s parent=%s" % (
        type(recv).__name__, title, isinstance(recv, (QDialog, QMainWindow)),
        parent or "-",
    )


class DiagApplication(QApplication):
    """覆写 notify：同步捕获每个被显示出来的顶层窗口（最可靠的弹窗抓取点）。"""

    def notify(self, receiver, event):  # noqa: N802
        try:
            if event.type() == QEvent.Type.Show:
                is_widget = getattr(receiver, "isWidgetType", None)
                if is_widget and receiver.isWidgetType() and receiver.isWindow():
                    get_logger().warning("SHOW-TOPLEVEL %s", _describe(receiver))
        except Exception:  # noqa: BLE001 - 绝不干扰主流程
            pass
        return super().notify(receiver, event)


def make_app(argv) -> QApplication:
    """构造带诊断的 QApplication（供 main.py 使用）。"""
    return DiagApplication(list(argv))


class _WindowWatchdog:
    """轮询所有顶层窗口，记录累积出现的窗口（兜底，覆盖未触发 Show 的顶层窗口）。"""

    def __init__(self, logger: logging.Logger, interval_ms: int = 250):
        self._logger = logger
        self._timer = QTimer()
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._tick()
        self._timer.start()

    def _tick(self) -> None:
        try:
            app = QApplication.instance()
            if app is None:
                return
            for w in app.topLevelWidgets():
                if not getattr(w, "isWidgetType", lambda: False)():
                    continue
                # 用 Qt 动态属性去重：不受 id() 复用影响（对象销毁后新对象无此属性）
                if w.property("_diag_logged"):
                    continue
                try:
                    w.setProperty("_diag_logged", True)
                except Exception:
                    pass
                try:
                    vis = w.isVisible()
                except Exception:
                    vis = "?"
                self._logger.warning("TOPLEVEL(seen) %s visible=%s", _describe(w), vis)
        except Exception:  # noqa: BLE001
            pass


def install_window_filter(app) -> None:
    global _watchdog_started
    if _watchdog_started:
        return
    logger = get_logger()
    wd = _WindowWatchdog(logger)
    wd.start()
    app.setProperty("_diag_watchdog", wd)
    _watchdog_started = True
    logger.info("diagnostics installed (notify hook + topLevel watchdog)")
