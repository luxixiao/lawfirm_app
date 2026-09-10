@echo off
setlocal
REM ===============================================================
REM  T3 VERIFY - one click pipeline (person settlement exporter)
REM    [1/4] dotnet build csharp\lawfirm.sln
REM    [2/4] dotnet run  --project csharp\LawFirm.Cli --report person
REM          -> csharp\out\person_settlement\*.xlsx  (one file per person)
REM    [3/4] python scripts\dump_golden.py --only person_settlement
REM    [4/4] python scripts\t3_filediff.py  -> dir-level paired diff
REM
REM  IMPORTANT - THIS FILE IS PURE ASCII ON PURPOSE.
REM  cmd.exe parses UTF-8 Chinese as GBK and garbles it, which
REM  produces wrong filenames and "not a command" spam. All Chinese
REM  text lives in scripts\t3_filediff.py (UTF-8 safe), never here.
REM
REM  Usage:  scripts\t3_verify.bat [year]     default: 2026
REM
REM  Real data run (recommended once real ledger is imported):
REM      scripts\t3_verify.bat 2025
REM
REM  Exit codes:
REM    0 = PASS  C# matches Python golden, all 7 dimensions + no pageSetup
REM    1 = build/run failed, or diffs found
REM    2 = workbook read error
REM    3 = golden or C# artifact missing
REM ===============================================================

cd /d "%~dp0.."

REM Output dir: csharp\out is gitignored so it does not exist after a fresh
REM clone or Seafile sync. Create it first, otherwise FileStream throws
REM "Could not find a part of the path".
if not exist "csharp\out" md "csharp\out"
if not exist "csharp\out\person_settlement" md "csharp\out\person_settlement"

set "YEAR=2026"
if not "%~1"=="" set "YEAR=%~1"

echo ===============================================================
echo  T3 VERIFY   year=%YEAR%
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

REM ---------- [2/4] run CLI (--report person) ----------
echo.
echo ==== [2/4] run LawFirm.Cli --report person to produce C# xlsx ====
"%DOTNET_EXE%" run --project csharp\LawFirm.Cli -- --report person --year %YEAR% --out-dir csharp\out\person_settlement
if errorlevel 1 (
    echo [FAIL ] LawFirm.Cli (--report person) failed
    pause
    exit /b 1
)

REM ---------- [3/4] golden ----------
echo.
echo ==== [3/4] regenerate Python golden (person_settlement only) ====
"%PY_EXE%" scripts\dump_golden.py --year %YEAR% --only person_settlement
if errorlevel 1 (
    echo [WARN ] dump_golden reported failures - continuing to diff
)

REM ---------- [4/4] diff ----------
echo.
echo ==== [4/4] dir-level diff golden vs C# ====
"%PY_EXE%" scripts\t3_filediff.py --year %YEAR% --candidate-dir csharp\out\person_settlement --verbose
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
