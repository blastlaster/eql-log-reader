@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  EQL Log Reader -- build_exe.bat
echo ============================================================
echo  Builds all four tools (Launcher, Friends Overlay, DPS/HPS
echo  Meter, Session Report) into one shared onedir bundle via
echo  eql_suite.spec. Output lands in "dist\EQL Log Reader\".
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3.8+ from https://python.org/downloads/
    echo         and check "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

if not exist ".buildenv" (
    echo Creating throwaway virtual environment in .buildenv ...
    python -m venv .buildenv
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

call ".buildenv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate the virtual environment.
    pause
    exit /b 1
)

echo.
echo Installing/upgrading PyInstaller in .buildenv ...
python -m pip install --upgrade pip >nul
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller.
    call deactivate
    pause
    exit /b 1
)

echo.
echo Cleaning previous build output (build\, dist\) ...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

echo.
echo Building eql_suite.spec (onedir, all five tools, UPX off) ...
pyinstaller --noconfirm --clean eql_suite.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed -- see the output above.
    call deactivate
    pause
    exit /b 1
)

call deactivate

if not exist "dist\EQL Log Reader\eql_launcher.exe" (
    echo [ERROR] Build finished but eql_launcher.exe is missing from
    echo         dist\EQL Log Reader\ -- something went wrong.
    pause
    exit /b 1
)

echo.
echo Copying icon.png, LICENSE, and README.md into dist\EQL Log Reader\ ...
if exist "icon.png"  copy /y "icon.png"  "dist\EQL Log Reader\icon.png"  >nul
if exist "LICENSE"   copy /y "LICENSE"   "dist\EQL Log Reader\LICENSE"   >nul
if exist "README.md" copy /y "README.md" "dist\EQL Log Reader\README.md" >nul

echo.
echo ============================================================
echo  Build complete: dist\EQL Log Reader\
echo    eql_launcher.exe
echo    eql_friend_overlay.exe
echo    eql_dps_meter.exe
echo    eql_session_report.exe
echo    eql_atlas.exe
echo    _internal\   (shared runtime/libraries -- do not remove)
echo.
echo  Next: run make_installer.bat to produce
echo        Output\EQL-Log-Reader-Setup.exe
echo ============================================================
pause
