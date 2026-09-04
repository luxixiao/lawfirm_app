# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 律所开票收款统计 v1.1.0

构建：在已安装依赖的 venv 中执行
    pyinstaller build_exe.spec --noconfirm --clean
产物：dist/律所开票收款统计.exe（带 Windows 版本信息，见 version_info.txt）

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
    "PySide6FramelessWindow",
    "openpyxl",
    "xlrd",
    "darkdetect",
]

extra_datas, extra_binaries, extra_hiddenimports = [], [], []
for pkg in ("qfluentwidgets", "PySide6FramelessWindow", "darkdetect"):
    try:
        d, b, h = collect_all(pkg)
        extra_datas += d
        extra_binaries += b
        extra_hiddenimports += h
    except Exception as e:  # pragma: no cover - 包不存在时跳过
        print(f"[build] collect_all 跳过 {pkg}: {e}")

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[os.getcwd()],
    binaries=extra_binaries,
    datas=extra_datas,
    hiddenimports=hiddenimports + extra_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
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
    name="律所开票收款统计",
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
    version="version_info.txt",  # 写入 Windows 版本信息（1.1.0）
    icon=None,
)
