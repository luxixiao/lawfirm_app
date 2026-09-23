"""诊断埋点门控（P2-1）单元测试 — offscreen。

默认（未设 LAWFIRM_DIAG=1）：
- make_app 返回普通 QApplication（不再覆写 notify 热路径）；
- install_window_filter 不启动看门狗（app 无 _diag_watchdog 属性）。

设 LAWFIRM_DIAG=1 后 enabled() 为真（诊断一键复开）。
运行：python tests/test_diag_gating.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("LAWFIRM_DIAG", None)  # 强制默认关闭路径

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from app import diag  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def main() -> int:
    check("默认 enabled()=False", diag.enabled() is False)
    os.environ["LAWFIRM_DIAG"] = "1"
    check("设 LAWFIRM_DIAG=1 后 enabled()=True", diag.enabled() is True)
    os.environ.pop("LAWFIRM_DIAG", None)

    app = QApplication.instance() or QApplication([])

    # 默认：make_app 返回普通 QApplication（每个进程只能建一个 QApplication，
    # DiagApplication 路径以 enabled() 断言代替实例化）
    got = diag.make_app([])
    check("默认 make_app 非 DiagApplication", not isinstance(got, diag.DiagApplication),
          f"got={type(got).__name__}")

    # 默认：install_window_filter 不装看门狗
    win = QMainWindow()
    diag.install_window_filter(win)
    check("默认 install_window_filter 无看门狗", win.property("_diag_watchdog") is None)
    win.close()

    # 文件日志：默认仍会挂 handler，但必须带轮转（RotatingFileHandler）且不在同步目录
    diag.setup_logging()
    import logging
    from logging.handlers import RotatingFileHandler
    lg = diag.get_logger()
    rfh = [h for h in lg.handlers if isinstance(h, RotatingFileHandler)]
    check("文件日志使用 RotatingFileHandler", len(rfh) >= 1,
          f"handlers={[type(h).__name__ for h in lg.handlers]}")
    if rfh:
        check("轮转参数 1MB x 3", rfh[0].maxBytes == 1024 * 1024 and rfh[0].backupCount == 3,
              f"maxBytes={rfh[0].maxBytes} backupCount={rfh[0].backupCount}")
        check("日志不在同步目录 data/ 下", "data" not in os.path.normpath(rfh[0].baseFilename)
              .split(os.sep)[-2:-1], rfh[0].baseFilename)

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
