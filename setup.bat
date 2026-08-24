@echo off
cd /d "%~dp0"

rem ============================================================
rem  Law Firm Billing Tool - one-click environment setup
rem  Creates venv at %USERPROFILE%\.lawfirm_venv and installs
rem  all dependencies (PySide6 6.11.1 + Fluent widgets + Excel).
rem  Run this once on each new machine, then use run_app.bat.
rem ============================================================

where python >nul 2>nul
if errorlevel 1 (
    echo [Error] Python not found.
    echo Please install Python 3.13 from https://www.python.org
    echo and check "Add python.exe to PATH" during install.
    pause
    exit /b 1
)

set "VENV=%USERPROFILE%\.lawfirm_venv"
set "PY=%VENV%\Scripts\python.exe"

if exist "%PY%" (
    echo [Info] venv already exists: %VENV%
) else (
    echo [1/3] Creating virtual environment ...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [Error] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [2/3] Upgrading pip ...
"%PY%" -m pip install --upgrade pip

echo [3/3] Installing dependencies ...
"%PY%" -m pip install PySide6==6.11.1 PySide6-Fluent-Widgets darkdetect PySideSix-Frameless-Window openpyxl xlrd
if errorlevel 1 (
    echo [Error] Dependency install failed. Check network and retry.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Setup complete! Run run_app.bat to start.
echo ============================================
pause
