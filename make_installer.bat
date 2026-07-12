@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  EQL Log Reader -- make_installer.bat
echo ============================================================
echo  Compiles installer.iss with Inno Setup's ISCC.exe into
echo  Output\EQL-Log-Reader-Setup.exe
echo ============================================================
echo.

if not exist "dist\EQL Log Reader\eql_launcher.exe" (
    echo [ERROR] dist\EQL Log Reader\eql_launcher.exe not found.
    echo         Run build_exe.bat first.
    pause
    exit /b 1
)

rem -- optional code signing (see BUILDING.md "Code signing") -----------
rem    Create signing.bat from signing.example.bat once you have a
rem    certificate; it sets EQL_SIGN to a "signtool sign ..." command
rem    prefix. When set, the four tool EXEs are signed here and Inno
rem    Setup signs the installer + uninstaller. Without it, the build
rem    is unchanged (unsigned, SmartScreen will warn users).
if exist "%~dp0signing.bat" call "%~dp0signing.bat"
if defined EQL_SIGN (
    echo Signing tool executables ...
    for %%F in (eql_launcher eql_friend_overlay eql_dps_meter eql_session_report) do (
        !EQL_SIGN! "dist\EQL Log Reader\%%F.exe"
        if errorlevel 1 (
            echo [ERROR] Signing dist\EQL Log Reader\%%F.exe failed.
            pause
            exit /b 1
        )
    )
    echo.
)

set "ISCC="

rem -- explicit, known install locations, newest first. Inno Setup 7
rem    defaults to a PER-USER install under %LocalAppData%\Programs
rem    (unlike 5/6, which defaulted to Program Files (x86)) -- check
rem    all three roots for each version. -------------------------------
for %%P in (
    "%LocalAppData%\Programs\Inno Setup 7\ISCC.exe"
    "%ProgramFiles%\Inno Setup 7\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
    "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%LocalAppData%\Programs\Inno Setup 5\ISCC.exe"
    "%ProgramFiles%\Inno Setup 5\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
) do (
    if not defined ISCC if exist %%P set "ISCC=%%~P"
)

rem -- version-agnostic fallback: scan all three roots for any "Inno Setup*"
if not defined ISCC (
    for /f "delims=" %%D in ('dir /b /ad "%LocalAppData%\Programs\Inno Setup*" 2^>nul') do (
        if not defined ISCC if exist "%LocalAppData%\Programs\%%D\ISCC.exe" set "ISCC=%LocalAppData%\Programs\%%D\ISCC.exe"
    )
)
if not defined ISCC (
    for /f "delims=" %%D in ('dir /b /ad "%ProgramFiles(x86)%\Inno Setup*" 2^>nul') do (
        if not defined ISCC if exist "%ProgramFiles(x86)%\%%D\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\%%D\ISCC.exe"
    )
)
if not defined ISCC (
    for /f "delims=" %%D in ('dir /b /ad "%ProgramFiles%\Inno Setup*" 2^>nul') do (
        if not defined ISCC if exist "%ProgramFiles%\%%D\ISCC.exe" set "ISCC=%ProgramFiles%\%%D\ISCC.exe"
    )
)

rem -- last resort: whatever's on PATH --------------------------------
if not defined ISCC (
    where ISCC.exe >nul 2>&1
    if not errorlevel 1 set "ISCC=ISCC.exe"
)

if not defined ISCC (
    echo [ERROR] Could not find ISCC.exe ^(Inno Setup's compiler^).
    echo         Install Inno Setup from https://jrsoftware.org/isdl.php,
    echo         or if it's already installed somewhere nonstandard,
    echo         open installer.iss in the Inno Setup IDE and click
    echo         Compile instead of running this script.
    pause
    exit /b 1
)

echo Using: %ISCC%
echo.
if defined EQL_SIGN (
    rem Inno's /S value can't carry raw quotes -- swap them for its $q
    rem placeholder so signtool paths with spaces survive.
    set "EQL_SIGN_ISCC=!EQL_SIGN:"=$q!"
    "%ISCC%" "/Seqlsign=!EQL_SIGN_ISCC! $f" /DSIGN "installer.iss"
) else (
    "%ISCC%" "installer.iss"
)
if errorlevel 1 (
    echo [ERROR] Inno Setup compile failed -- see the output above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Installer built: Output\EQL-Log-Reader-Setup.exe
echo  That's the one file to share/upload -- double-click it,
echo  click through, done.
echo ============================================================
pause
