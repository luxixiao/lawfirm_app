@echo off
cd /d "%~dp0"

rem ============================================================
rem  律所开票收款统计 — 打包 EXE（v1.1.0）
rem  前置：已运行 setup.bat 创建 venv 并安装依赖
rem  本脚本仅在本机（已装依赖的 venv）执行，产出 dist\律所开票收款统计.exe
rem ============================================================

set "PY="

if exist "%USERPROFILE%\.lawfirm_venv\Scripts\python.exe" (
    set "PY=%USERPROFILE%\.lawfirm_venv\Scripts\python.exe"
) else if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else if exist "C:\Users\big\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
    set "PY=C:\Users\big\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)

if not defined PY (
    echo.
    echo [Error] 未找到 Python 环境，请先运行 setup.bat。
    echo.
    pause
    exit /b 1
)

echo Using Python: %PY%

echo [1/2] 确保 pyinstaller 已安装 ...
"%PY%" -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [Error] pyinstaller 安装失败，请检查网络后重试。
    pause
    exit /b 1
)

echo [2/2] 构建 EXE ...
"%PY%" -m PyInstaller build_exe.spec --noconfirm --clean
if errorlevel 1 (
    echo [Error] 构建失败，请查看上方日志。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   构建完成：dist\律所开票收款统计.exe
echo   版本信息：文件/产品版本 1.1.0（见 version_info.txt）
echo ============================================================
pause
