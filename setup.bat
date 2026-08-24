@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ============================================================
rem  律所开票收款统计 - 一键环境安装（新机器首次使用）
rem  在 %USERPROFILE%\.lawfirm_venv 创建虚拟环境并安装全部依赖
rem  （放用户目录，避免被 Seafile/网盘同步大文件）
rem  装完即可双击 run_app.bat 启动
rem ============================================================

echo ============================================
echo   律所开票收款统计 - 一键环境安装
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.13 并勾选 "Add python.exe to PATH"
    echo        下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "delims=" %%v in ('python --version 2^>^&1') do echo 系统 Python: %%v

set "VENV=%USERPROFILE%\.lawfirm_venv"
set "PY=%VENV%\Scripts\python.exe"

if exist "%PY%" (
    echo [提示] 已存在虚拟环境 %VENV%，跳过创建
) else (
    echo [1/3] 创建虚拟环境 ...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败
        pause
        exit /b 1
    )
)

echo [2/3] 升级 pip ...
"%PY%" -m pip install --upgrade pip

echo [3/3] 安装依赖（PySide6 6.11.1 + Fluent 控件 + Excel 支持）...
"%PY%" -m pip install PySide6==6.11.1 PySide6-Fluent-Widgets darkdetect PySideSix-Frameless-Window openpyxl xlrd
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试
    pause
    exit /b 1
)

echo.
echo ============================================
echo   安装完成！以后双击 run_app.bat 即可启动。
echo ============================================
pause
