@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ============================================================
rem  律所开票收款统计 - 启动器（跨机器版）
rem  自动定位 Python 环境，按顺序尝试：
rem   1) 项目内 .venv（如有）
rem   2) %USERPROFILE%\.lawfirm_venv（setup.bat 创建的）
rem   3) 旧版固定路径（本机历史环境）
rem  都没有 → 提示先运行 setup.bat 一键安装
rem ============================================================

set "PY="

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else if exist "%USERPROFILE%\.lawfirm_venv\Scripts\python.exe" (
    set "PY=%USERPROFILE%\.lawfirm_venv\Scripts\python.exe"
) else if exist "C:\Users\big\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
    set "PY=C:\Users\big\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)

if not defined PY (
    echo.
    echo [提示] 未找到 Python 运行环境。
    echo        首次使用请先双击 setup.bat 一键安装（自动创建虚拟环境并安装依赖）。
    echo.
    pause
    exit /b 1
)

echo 使用环境: %PY%
"%PY%" main.py
pause
