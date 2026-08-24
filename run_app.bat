@echo off
cd /d "%~dp0"

rem ============================================================
rem  Law Firm Billing Tool - launcher (cross-machine)
rem  Python resolution order:
rem   1) project-local .venv
rem   2) %USERPROFILE%\.lawfirm_venv   (created by setup.bat)
rem   3) legacy fixed path (existing machines)
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
    echo [Error] Python environment not found.
    echo Please run setup.bat once to create it and install dependencies.
    echo.
    pause
    exit /b 1
)

echo Using Python: %PY%
"%PY%" main.py
pause
