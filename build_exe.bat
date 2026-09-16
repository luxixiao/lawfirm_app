@echo off
cd /d "%~dp0"

set "LOG=%~dp0build_log.txt"
set "RC=0"
echo ============================================ > "%LOG%"
echo  lawfirm_app EXE build log >> "%LOG%"
echo  started: %date% %time% >> "%LOG%"
echo ============================================ >> "%LOG%"

call :build >> "%LOG%" 2>&1
set "RC=%errorlevel%"

echo. >> "%LOG%"
if %RC%==0 (
  echo [OK] Build succeeded - see the .exe under dist\ >> "%LOG%"
) else (
  echo [FAIL] Build failed (exit code %RC%) - check the log above >> "%LOG%"
)
echo ============================================ >> "%LOG%"

echo.
echo  Log written to: %LOG%
echo.
type "%LOG%"
echo.
pause
goto :eof

:build
set "PY="

if exist "%USERPROFILE%\.lawfirm_venv\Scripts\python.exe" (
    set "PY=%USERPROFILE%\.lawfirm_venv\Scripts\python.exe"
) else if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else if exist "C:\Users\big\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
    set "PY=C:\Users\big\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)

if not defined PY (
    echo [ERROR] No Python environment found. Run setup.bat first to create venv.
    exit /b 1
)
echo Using Python: %PY%
"%PY%" --version

echo [1/3] Checking and installing build deps (pyinstaller / PySide6FramelessWindow)...
"%PY%" -m pip install "pyinstaller==6.22.3"
if errorlevel 1 (
    echo [ERROR] pyinstaller install failed. Check your network.
    exit /b 1
)
"%PY%" -c "import importlib.util as u; raise SystemExit(0 if u.find_spec('PySide6FramelessWindow') else 1)" 2>nul
if errorlevel 1 (
    echo PySide6FramelessWindow missing, installing PySideSix-Frameless-Window ...
    "%PY%" -m pip install PySideSix-Frameless-Window
    if errorlevel 1 (
        echo [ERROR] PySideSix-Frameless-Window install failed.
        exit /b 1
    )
)

echo [2/3] Running PyInstaller (build_exe.spec)...
"%PY%" -m PyInstaller build_exe.spec --noconfirm --clean
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

echo [3/3] Done.
exit /b 0
