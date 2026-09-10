@echo off
setlocal
REM ===============================================================
REM  T2 VERIFY - one click pipeline
REM    [1/4] dotnet build csharp\lawfirm.sln
REM    [2/4] dotnet run  --project csharp\LawFirm.Cli   -> C# xlsx
REM    [3/4] python scripts\dump_golden.py              -> Python golden
REM    [4/4] python scripts\t2_diff.py                  -> cell-by-cell diff
REM
REM  IMPORTANT - THIS FILE IS PURE ASCII ON PURPOSE.
REM  cmd.exe parses UTF-8 Chinese as GBK and garbles it, which
REM  produces wrong filenames and "not a command" spam. All Chinese
REM  text lives in scripts\t2_diff.py (UTF-8 safe), never here.
REM
REM  Usage:  scripts\t2_verify.bat [year] [month]     default: 2026 12
REM
REM  Real data run (recommended once real ledger is imported):
REM      scripts\t2_verify.bat 2025 12
REM
REM  Exit codes:
REM    0 = PASS  C# matches Python golden, all 7 dimensions
REM    1 = build/run failed, or diffs found
REM    2 = workbook read error
REM    3 = golden or C# artifact missing
REM ===============================================================

cd /d "%~dp0.."

REM Output dir: csharp\out is gitignored so it does not exist after a fresh
REM clone or Seafile sync. Create it first, otherwise FileStream throws
REM "Could not find a part of the path".
if not exist "csharp\out" md "csharp\out"

set "YEAR=2026"
set "MONTH=12"
if not "%~1"=="" set "YEAR=%~1"
if not "%~2"=="" set "MONTH=%~2"

echo ===============================================================
echo  T2 VERIFY   year=%YEAR%   month=%MONTH%
echo ===============================================================

REM ---------- locate dotnet ----------
set "DOTNET_EXE="
for /f "delims=" %%i in ('where dotnet 2^>nul') do (
    if not defined DOTNET_EXE set "DOTNET_EXE=%%i"
)
if not defined DOTNET_EXE if exist "%USERPROFILE%\.dotnet\dotnet.exe" set "DOTNET_EXE=%USERPROFILE%\.dotnet\dotnet.exe"
if not defined DOTNET_EXE (
    echo [ERROR] dotnet.exe not found on PATH, and not at %USERPROFILE%\.dotnet\dotnet.exe
    echo [HINT ] Install .NET 10 SDK, or add it to PATH.
    pause
    exit /b 1
)
echo [INFO ] dotnet: %DOTNET_EXE%
"%DOTNET_EXE%" --version

REM ---------- locate python ----------
set "PY_EXE=%USERPROFILE%\.lawfirm_venv\Scripts\python.exe"
if not exist "%PY_EXE%" (
    echo [WARN ] venv python not found: %PY_EXE%
    echo [WARN ] falling back to system python
    set "PY_EXE=python"
)
echo [INFO ] python: %PY_EXE%

REM ---------- [1/4] build ----------
echo.
echo ==== [1/4] dotnet build csharp\lawfirm.sln ====
REM -m:1 -nodereuse:false : MSBuild node reuse occasionally hangs or gets
REM   killed on some machines. Single node is stable; only 4 tiny projects.
"%DOTNET_EXE%" build csharp\lawfirm.sln -m:1 -nodereuse:false
if errorlevel 1 (
    echo [FAIL ] build failed - see errors above
    pause
    exit /b 1
)

REM ---------- [2/4] run CLI ----------
echo.
echo ==== [2/4] run LawFirm.Cli to produce C# xlsx ====
set "OUT=csharp\out\settlement_report_csharp.xlsx"
"%DOTNET_EXE%" run --project csharp\LawFirm.Cli -- --year %YEAR% --month %MONTH% --out %OUT%
if errorlevel 1 (
    echo [FAIL ] LawFirm.Cli failed
    pause
    exit /b 1
)

REM ---------- [3/4] golden ----------
echo.
echo ==== [3/4] regenerate Python golden ====
"%PY_EXE%" scripts\dump_golden.py --year %YEAR% --report-month %MONTH%
if errorlevel 1 (
    echo [WARN ] dump_golden reported failures - continuing to diff
)

REM ---------- [4/4] diff ----------
echo.
echo ==== [4/4] diff golden vs C# ====
"%PY_EXE%" scripts\t2_diff.py --year %YEAR% --month %MONTH% --candidate %OUT% --verbose
set "RC=%ERRORLEVEL%"

echo.
echo ===============================================================
if "%RC%"=="0" echo [PASS ] C# output matches Python golden. Exit=%RC%
if not "%RC%"=="0" echo [FAIL ] exit code %RC% - see diff output above
echo         0=pass  1=diffs  2=read error  3=missing artifact
echo ===============================================================
echo [DONE] Copy the output above and paste it to the assistant.
pause
exit /b %RC%
