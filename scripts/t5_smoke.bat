@echo off
REM ============================================================
REM  T5 smoke test: build solution + ComputeFill self-test.
REM
REM  Usage:  double-click, or  scripts\t5_smoke.bat
REM
REM  Steps:
REM    [1/3] locate dotnet SDK (%USERPROFILE%\.dotnet or PATH)
REM    [2/3] dotnet build csharp\lawfirm.sln   (0 errors required)
REM    [3/3] dotnet run --project LawFirm.Cli -- --selftest-ui
REM          -> runs 5 ComputeFill unit tests (exit 0 = all green)
REM
REM  NOTE: this script does NOT launch the WPF shell. Manual checks:
REM    - run LawFirm.App, press F12  -> two-tier header min sample
REM      (verify: frozen col + pixel scroll + span-width sync)
REM    - navigate sidebar to the settlement page
REM    - compare visuals against Python app screenshots
REM
REM  Exit codes: 0 = PASS, non-zero = first failing step.
REM  Pure ASCII only (cmd.exe parses this file with the ANSI codepage).
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0.."

REM ---- [0/3] guard env vars some shells lack (NuGet needs APPDATA) ----
if "%APPDATA%"=="" set "APPDATA=%USERPROFILE%\AppData\Roaming"
if not defined ProgramFiles set "ProgramFiles=C://Program Files"
if not defined ProgramW6432 set "ProgramW6432=C://Program Files"

REM ---- [1/3] resolve dotnet (double-click has no dev PATH) ----
set "DOTNET=dotnet"
if exist "%USERPROFILE%\.dotnet\dotnet.exe" set "DOTNET=%USERPROFILE%\.dotnet\dotnet.exe"
where dotnet >nul 2>&1
if not errorlevel 1 set "DOTNET=dotnet"

echo [1/3] using dotnet: %DOTNET%
"%DOTNET%" --version >nul 2>&1
if errorlevel 1 (
    echo [FAIL] dotnet SDK not found. Expected at %USERPROFILE%\.dotnet\dotnet.exe
    echo.
    pause
    exit /b 3
)

echo.
echo [2/3] dotnet build csharp\lawfirm.sln
"%DOTNET%" build csharp\lawfirm.sln -v m
if errorlevel 1 (
    echo [FAIL] build failed
    echo.
    pause
    exit /b 1
)

echo.
echo [3/3] ComputeFill self-test
"%DOTNET%" run --no-build --project csharp\LawFirm.Cli -- --selftest-ui
if errorlevel 1 (
    echo [FAIL] self-test failed
    echo.
    pause
    exit /b 2
)

echo.
echo [PASS] T5 smoke: build + self-test OK.
echo Next manual steps:
echo   1. run:  %DOTNET% run --project csharp\LawFirm.App
echo   2. press F12 for the two-tier min sample
echo      (frozen col stays put + pixel scroll + group header spans)
echo   3. open the settlement page from the sidebar
echo   4. export all staff (async progress; main window stays draggable)
echo.
pause
endlocal
exit /b 0
