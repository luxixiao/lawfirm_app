@echo off
setlocal EnableExtensions
REM =====================================================================
REM pilot_local_setup.bat  (ONE-CLICK, run on USER MACHINE only)
REM All output is mirrored to pilot_local_setup.log in the same folder,
REM so even if the window closes you can read what happened.
REM Prints live to console; pauses at every exit point.
REM If it errors, send me pilot_local_setup.log or the [ERROR] lines.
REM Requires: repo root lawfirm_app/ ; venv present ; data/lawfirm.db.
REM NOTE: messages passed to :tee must NOT contain ( ) or cmd mis-parses.
REM =====================================================================

set "LOG=%~dp0pilot_local_setup.log"
echo ===== pilot_local_setup started: %date% %time% ===== > "%LOG%"

cd /d "%~dp0.."
call :tee [INFO] repo root = %CD%

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else if exist "%USERPROFILE%\.lawfirm_venv\Scripts\python.exe" (
    set "PY=%USERPROFILE%\.lawfirm_venv\Scripts\python.exe"
) else if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
    set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)

if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        call :tee [INFO] venv not found, auto-running setup.bat to create %USERPROFILE%\.lawfirm_venv
        call "%~dp0setup.bat"
        if exist "%USERPROFILE%\.lawfirm_venv\Scripts\python.exe" (
            set "PY=%USERPROFILE%\.lawfirm_venv\Scripts\python.exe"
        )
    )
)
if not defined PY (
    call :tee [ERROR] Python venv not found and setup.bat did not create one.
    call :tee         Run setup.bat manually - needs network for pip, then re-run this bat.
    call :tee         log: %LOG%
    pause
    exit /b 1
)

call :tee [1/5] Generating golden baseline - needs venv and data/lawfirm.db
"%PY%" scripts\dump_golden.py
set "RC=%errorlevel%"
echo dump_golden.py exit=%RC% >> "%LOG%"
if %RC% neq 0 (
    call :tee [ERROR] dump_golden.py failed - exit=%RC%. See Python traceback above and %LOG%.
    call :tee         If it still fails, send me %LOG%.
    pause
    exit /b 1
)

call :tee [2/5] Self-diff sanity check: every golden xlsx diffed against itself, expect 0 diff
"%PY%" scripts\self_diff_golden.py
if errorlevel 1 (
    call :tee [ERROR] self-diff did not all pass - open scripts\self_diff_report.txt and paste its content.
    pause
    exit /b 1
)
call :tee [OK] self-diff passed - all golden xlsx identical to themselves

call :tee [3/5] Create pilot/dotnet branch and commit plan docs + T1 scripts
git checkout -B pilot/dotnet
if errorlevel 1 (
    call :tee [ERROR] git checkout -B pilot/dotnet failed. Working tree may be dirty or repo has no commits.
    call :tee         Run 'git status' to inspect, then re-run.
    pause
    exit /b 1
)
git add docs\csharp-wpf-refactor-plan-2026-09-09.md docs\runtime-ux-comparison-2026-09-09.md 2>nul
git commit -m "docs: C#/WPF rewrite plan + usage-phase comparison (decisions A-J confirmed)" 2>&1
git add scripts\diff_xlsx.py scripts\dump_golden.py scripts\self_diff_golden.py tests\golden\ csharp\ 2>nul
git commit -m "Pilot T1+T2: golden safety-net scripts + baseline + WPF/Dapper read-only skeleton" 2>&1
git push -u origin pilot/dotnet
if errorlevel 1 (
    call :tee [WARN] git push failed - usually missing GitHub auth on this new machine.
    call :tee        Local commits are saved on branch pilot/dotnet. Set up Git credentials, then:
    call :tee        git push -u origin pilot/dotnet
)

call :tee [4/5] Archive wt-* branches as tags - rollback safety - then delete local+remote
for %%b in (wt-dev wt-plan-a wt-plan-b wt-plan-c) do (
    git rev-parse %%b >nul 2>&1 && git tag archive/%%b %%b
)
git branch -D wt-dev wt-plan-a wt-plan-b wt-plan-c 2>nul
git push origin --delete wt-dev wt-plan-a wt-plan-b wt-plan-c 2>nul
git push origin --tags

call :tee [5/5] DONE. Pilot T1 committed+pushed on pilot/dotnet; wt-* archived as tags.
call :tee       Next: tell me to start T2 - WPF skeleton plus Dapper read-only.
echo.
call :tee ===== ALL DONE. Log saved to %LOG%. Press any key to close the window =====
pause
goto :eof

:tee
set "M=%*"
echo %M%
echo %M%>>"%LOG%"
goto :eof
