@echo off
cd /d "%~dp0"

rem ============================================================
rem  律所开票收款统计 — 打包 EXE（v1.1.0）
rem  用法：双击本文件，或在 cmd 中 cd 到本目录后运行 build_exe.bat
rem  全部输出会写入 build_log.txt，构建结束前窗口不会自动关闭
rem ============================================================

set "LOG=%~dp0build_log.txt"
set "RC=0"
echo ============================================ > "%LOG%"
echo  律所开票收款统计 EXE 构建日志 >> "%LOG%"
echo  开始时间: %date% %time% >> "%LOG%"
echo ============================================ >> "%LOG%"

call :build >> "%LOG%" 2>&1
set "RC=%errorlevel%"

echo. >> "%LOG%"
if %RC%==0 (
  echo [OK] 构建成功：dist\律所开票收款统计.exe >> "%LOG%"
) else (
  echo [FAIL] 构建失败（退出码 %RC%），请查看上方日志 >> "%LOG%"
)
echo ============================================ >> "%LOG%"

echo.
echo  日志已写入：%LOG%
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
    echo [错误] 未找到 Python 环境，请先运行 setup.bat 创建 venv。
    exit /b 1
)
echo 使用 Python: %PY%
"%PY%" --version

echo [1/3] 校验并补齐构建依赖（pyinstaller / PySide6FramelessWindow）...
"%PY%" -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [错误] pyinstaller 安装失败，请检查网络后重试。
    exit /b 1
)
"%PY%" -c "import importlib.util as u; raise SystemExit(0 if u.find_spec('PySide6FramelessWindow') else 1)" 2>nul
if errorlevel 1 (
    echo 检测到 PySide6FramelessWindow 缺失，正在安装 PySideSix-Frameless-Window ...
    "%PY%" -m pip install PySideSix-Frameless-Window
    if errorlevel 1 (
        echo [错误] PySideSix-Frameless-Window 安装失败。
        exit /b 1
    )
)

echo [2/3] 运行 PyInstaller 构建 EXE（build_exe.spec）...
"%PY%" -m PyInstaller build_exe.spec --noconfirm --clean
if errorlevel 1 (
    echo [错误] PyInstaller 构建失败，详见上方日志。
    exit /b 1
)

echo [3/3] 完成。
exit /b 0
