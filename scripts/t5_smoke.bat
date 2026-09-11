@echo off
REM ============================================================
REM  T5 smoke test: build solution + ComputeFill self-test.
REM
REM  Usage:  scripts\t5_smoke.bat
REM
REM  Steps:
REM    [1/2] dotnet build csharp\lawfirm.sln        (0 errors required)
REM    [2/2] dotnet run --project LawFirm.Cli -- --selftest-ui
REM          -> runs 4 ComputeFill unit tests (exit 0 = all green)
REM
REM  NOTE: this script does NOT launch the WPF shell. Manual checks:
REM    - run LawFirm.App, press F12  -> two-tier header min sample
REM      (verify: frozen col + pixel scroll + span-width sync)
REM    - navigate sidebar to "leibie baobiao" (settlement page)
REM    - compare visuals against Python app screenshots
REM
REM  Exit codes: 0 = PASS, non-zero = first failing step.
REM  Pure ASCII only (cmd.exe parses this file with the ANSI codepage).
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0.."

echo [1/2] dotnet build csharp\lawfirm.sln
dotnet build csharp\lawfirm.sln -v m
if errorlevel 1 (
    echo [FAIL] build failed
    exit /b 1
)

echo [2/2] ComputeFill self-test
dotnet run --no-build --project csharp\LawFirm.Cli -- --selftest-ui
if errorlevel 1 (
    echo [FAIL] self-test failed
    exit /b 2
)

echo.
echo [PASS] T5 smoke: build + self-test OK.
echo Next manual steps:
echo   1. start LawFirm.App, press F12 for the two-tier min sample
echo   2. open the settlement page from the sidebar
echo   3. export all staff (async progress; main window stays draggable)
endlocal
exit /b 0
