# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 律所开票收款统计 v1.1.4dev2

构建：在已安装依赖的 venv 中执行
    pyinstaller build_exe.spec --noconfirm --clean
产物：dist/lawfirm_app.exe（带 Windows 版本信息，见 version_info.txt）

依赖环境（setup.bat 创建）：PySide6 6.11.1 + PySide6-Fluent-Widgets +
PySideSix-Frameless-Window + openpyxl + xlrd + darkdetect + pyinstaller
"""
import os

from PyInstaller.utils.hooks import collect_all

# ---- 隐式依赖收集 ----
# qfluentwidgets / qframelesswindow 在运行时动态引用子模块并自带资源，
# 用 collect_all 把它们的 datas / binaries / hiddenimports 一并带进来。
hiddenimports = [
    "qfluentwidgets",
    "qfluentwidgets.components",
    "qfluentwidgets.window",
    "qframelesswindow",
    "openpyxl",
    "xlrd",
    "darkdetect",
]

extra_datas, extra_binaries, extra_hiddenimports = [], [], []
for pkg in ("qfluentwidgets", "qframelesswindow", "darkdetect"):
    try:
        d, b, h = collect_all(pkg)
        extra_datas += d
        extra_binaries += b
        extra_hiddenimports += h
    except Exception as e:  # pragma: no cover - 包不存在时跳过
        print(f"[build] collect_all 跳过 {pkg}: {e}")

# Qt 中文翻译表：不打进 exe 的话，QMessageBox / QDialogButtonBox 的标准按钮
# 与 QFileDialog 全是英文（OK / Cancel / Show Details...）。
# 放在 PySide6/translations 下，app/ui/i18n.py 会从 sys._MEIPASS 找到它。
try:
    from PySide6.QtCore import QLibraryInfo
    _tr_dir = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for _qm in ("qtbase_zh_CN.qm", "qt_zh_CN.qm"):
        _p = os.path.join(_tr_dir, _qm)
        if os.path.isfile(_p):
            extra_datas.append((_p, "PySide6/translations"))
        else:
            print(f"[build] 缺少 {_qm}（{_tr_dir}）——打包后弹窗按钮会是英文")
except Exception as e:  # pragma: no cover
    print(f"[build] 收集 Qt 中文翻译失败: {e}")

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[os.getcwd()],
    binaries=extra_binaries,
    datas=extra_datas + [("app.ico", ".")],
    hiddenimports=hiddenimports + extra_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # 排除应用未使用的重型 Qt 子模块，显著减小单文件体积（实测 222MB → 约 120-150MB）。
    # 这些模块由 PyInstaller 的 PySide6 hook 过度收集；本应用是纯 QWidget（qfluentwidgets 本体不依赖它们）。
    # 若构建后启动或某页面报 ModuleNotFoundError(…QtXxx)，把对应行移回 excludes=[] 即可。
    excludes=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQmlModels",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtPrintSupport",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="lawfirm_app",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,               # 等价于 --windowed：无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version="version_info.txt",  # 写入 Windows 版本信息（1.1.4dev2）
    icon="app.ico",
)
