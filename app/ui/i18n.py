"""Qt 内置文案中文化 —— 让标准按钮 / 文件对话框显示中文。

问题：PySide6 默认不装载 Qt 的中文翻译，因此 QMessageBox、QDialogButtonBox 的
标准按钮一律是英文（OK / Cancel / Yes / No / Close / Show Details...），
QFileDialog 也是英文界面。业务代码里 60+ 处 QDialogButtonBox/StandardButton 都受此影响。

做法：启动时从 Qt translations 目录装载 `qtbase_zh_CN`（缺失时退回 `qt_zh_CN`），
installTranslator 之后 Qt 自己会把上述内置文案翻成「确定 / 取消 / 是 / 否 /
关闭 / 显示详情...」。本模块只负责"提供中文"，个别按钮仍可用 setText 覆盖文案。

注意：
- QTranslator 必须保活（Qt 只持裸指针，对象被 GC 后翻译立刻失效），故存在
  `_TRANSLATORS` 列表里。
- 打包成 exe 后 Qt 的 translations 目录不在原位，故 build_exe.spec 会把
  qtbase_zh_CN.qm / qt_zh_CN.qm 显式收进 PySide6/translations；本模块按
  sys._MEIPASS 一并查找。
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator

_QM_NAMES = ("qtbase_zh_CN", "qt_zh_CN")

# 保活用：QTranslator 被回收后翻译立即失效
_TRANSLATORS: list[QTranslator] = []
_LOADED: list[str] = []
_DONE = False


def _candidate_dirs() -> list[str]:
    """查找 .qm 的目录（开发期 Qt 安装目录 + 打包后的 _MEIPASS）。"""
    dirs = [QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)]
    base = getattr(sys, "_MEIPASS", "")
    if base:
        dirs.append(os.path.join(base, "PySide6", "translations"))
        dirs.append(os.path.join(base, "translations"))
    out, seen = [], set()
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def install_qt_zh(app: QCoreApplication | None = None) -> list[str]:
    """装载 Qt 中文翻译；返回实际装载的 .qm 名列表（幂等，可重复调用）。"""
    global _DONE
    if _DONE:
        return list(_LOADED)
    app = app or QCoreApplication.instance()
    if app is None:  # 没有 QApplication 时装不了，下次再试
        return []
    _DONE = True
    for name in _QM_NAMES:
        for d in _candidate_dirs():
            tr = QTranslator(app)
            if not tr.load(name, d):
                continue
            if app.installTranslator(tr):
                _TRANSLATORS.append(tr)
                _LOADED.append(name)
            break
    return list(_LOADED)


def installed() -> list[str]:
    """已装载的 .qm 名（供自检/日志用）。"""
    return list(_LOADED)
