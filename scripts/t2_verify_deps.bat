@echo off
setlocal
REM ===============================================================
REM  T2 VERIFY DEPS - confirm CPM transitive pinning actually took effect
REM
REM  Purpose: the CVE version locks in csharp\Directory.Packages.props
REM  only apply to TRANSITIVE dependencies when
REM  CentralPackageTransitivePinningEnabled=true is set. Without that
REM  switch the pinned versions are silently ignored for packages that
REM  NPOI / Microsoft.Data.Sqlite pull in (ImageSharp, Cryptography.Xml,
REM  Cryptography.Pkcs, SQLitePCLRaw). This script proves the pinning is
REM  live by reading the ACTUAL resolved versions from the restore.
REM
REM  It does three things, in order:
REM    [1/3] dotnet restore csharp\lawfirm.sln
REM    [2/3] dotnet list ... package --include-transitive
REM    [3/3] extract the pinned packages' resolved versions from
REM          csharp\LawFirm.Cli\obj\project.assets.json (via PowerShell)
REM
REM  Expected versions (must match csharp\Directory.Packages.props):
REM    SixLabors.ImageSharp                       2.1.13
REM    System.Security.Cryptography.Xml          10.0.12
REM    System.Security.Cryptography.Pkcs         10.0.12
REM    SQLitePCLRaw.bundle_e_sqlite3              2.1.13
REM    SQLitePCLRaw.lib.e_sqlite3                 2.1.13  (pulled by bundle)
REM
REM  IMPORTANT - THIS FILE IS PURE ASCII ON PURPOSE.
REM  cmd.exe parses UTF-8 Chinese as GBK and garbles it, which
REM  produces wrong filenames and "not a command" spam. Keep all
REM  Chinese text out of .bat files (see scripts\t2_verify.bat too).
REM
REM  Usage:  scripts\t2_verify_deps.bat
REM
REM  Exit codes:
REM    0 = all pinned packages resolved to the expected versions
REM    1 = dotnet not found, or restore/list failed
REM    2 = project.assets.json missing (restore did not produce it)
REM    3 = at least one package resolved to a NON-pinned (wrong) version
REM ===============================================================

cd /d "%~dp0.."

echo ===============================================================
echo  T2 VERIFY DEPS - CPM transitive pinning check
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

REM ---------- [1/3] restore ----------
echo.
echo ==== [1/3] dotnet restore csharp\lawfirm.sln ====
"%DOTNET_EXE%" restore csharp\lawfirm.sln
if errorlevel 1 (
    echo [FAIL ] restore failed - see errors above
    pause
    exit /b 1
)

REM ---------- [2/3] list resolved packages (transitive) ----------
echo.
echo ==== [2/3] resolved packages incl. transitive ====
echo          (look for ImageSharp / Cryptography.Xml / Cryptography.Pkcs / SQLitePCLRaw)
"%DOTNET_EXE%" list csharp\LawFirm.Cli\LawFirm.Cli.csproj package --include-transitive
if errorlevel 1 (
    echo [FAIL ] dotnet list failed - see errors above
    pause
    exit /b 1
)

REM ---------- [3/3] extract actual versions from project.assets.json ----------
echo.
echo ==== [3/3] actual resolved versions from project.assets.json ====
set "ASSETS=csharp\LawFirm.Cli\obj\project.assets.json"
if not exist "%ASSETS%" (
    echo [FAIL ] assets file not found: %ASSETS%
    echo [HINT ] restore did not produce it; run this script from the repo root.
    pause
    exit /b 2
)

REM Parse the JSON with PowerShell. The "targets" section keys are of the
REM form "<PackageId>/<Version>", e.g. "SixLabors.ImageSharp/2.1.13".
REM Each pinned id's resolved version is compared to the expected value.
REM (PowerShell is used instead of findstr because parsing JSON reliably
REM  needs structural awareness, not substring matching.)
set "PS1=%TEMP%\t2_verify_deps_%RANDOM%.ps1"
> "%PS1%"  echo $ErrorActionPreference = 'Stop'
>>"%PS1%" echo $assets = '%~dp0..\csharp\LawFirm.Cli\obj\project.assets.json'
>>"%PS1%" echo if (-not (Test-Path -LiteralPath $assets)) { Write-Host "[FAIL] assets not found"; exit 2 }
>>"%PS1%" echo $expect = [ordered]@{ 'SixLabors.ImageSharp' = '2.1.13'; 'System.Security.Cryptography.Xml' = '10.0.12'; 'System.Security.Cryptography.Pkcs' = '10.0.12'; 'SQLitePCLRaw.bundle_e_sqlite3' = '2.1.13'; 'SQLitePCLRaw.lib.e_sqlite3' = '2.1.13' }
>>"%PS1%" echo $json = Get-Content -Raw -LiteralPath $assets ^| ConvertFrom-Json
>>"%PS1%" echo $resolved = @{}
>>"%PS1%" echo foreach ($t in $json.targets.PSObject.Properties) { foreach ($p in $t.Value.PSObject.Properties) { $k = $p.Name; $s = $k.LastIndexOf('/'); if ($s -lt 1) { continue }; $id = $k.Substring(0,$s); $ver = $k.Substring($s+1); if (-not $resolved.ContainsKey($id)) { $resolved[$id] = $ver } } }
>>"%PS1%" echo $rc = 0
>>"%PS1%" echo foreach ($k in $expect.Keys) { $want = $expect[$k]; if ($resolved.ContainsKey($k)) { $got = $resolved[$k]; if ($got -eq $want) { Write-Host ('[OK  ] {0} = {1}' -f $k, $got) } else { Write-Host ('[BAD ] {0} = {1} (expected {2}) - transitive pinning NOT effective' -f $k, $got, $want); $rc = 3 } } else { Write-Host ('[MISS] {0} not present (expected {1})' -f $k, $want); $rc = 3 } }
>>"%PS1%" echo exit $rc

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
set "RC=%ERRORLEVEL%"
del "%PS1%" >nul 2>nul

echo.
echo ===============================================================
if "%RC%"=="0" echo [PASS ] all pins resolved to expected versions. Exit=0
if not "%RC%"=="0" echo [FAIL ] one or more packages resolved to a WRONG version. Exit=%RC%
echo ===============================================================
echo [DONE] Copy the output above and paste it to the assistant.
pause
exit /b %RC%
