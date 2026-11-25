@echo off
setlocal
cd /d "%~dp0"
title Seedr Movie Manager - Build

echo ============================================================
echo  Building Seedr Movie Manager
echo  Folder: %CD%
echo ============================================================
echo.

REM ---- Check required files ----
for %%F in (main.py backend.py secure_store.py icon.ico icon.png installer.iss web\index.html web\style.css web\app.js) do (
    if not exist "%%F" (
        echo ERROR: %%F is missing from this folder.
        goto :fail
    )
)

REM ---- Find Python ----
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo ERROR: Python was not found.
    echo Install Python 3 from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    goto :fail
)
echo Using: %PY%
%PY% --version
echo.

echo [1/3] Installing build requirements...
%PY% -m pip install --upgrade pip
%PY% -m pip install requests seedrcc pywebview pyinstaller
if errorlevel 1 goto :fail

echo.
echo [2/3] Building the app with PyInstaller (this takes a minute)...
%PY% -m PyInstaller --noconfirm --clean --windowed --onedir --name SeedrMovieManager --icon icon.ico --add-data "icon.ico;." --add-data "icon.png;." --add-data "web;web" --collect-submodules seedrcc --collect-all webview --collect-all pythonnet --collect-all clr_loader main.py
if errorlevel 1 goto :fail

echo.
echo [3/3] Building the installer with Inno Setup...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"

if not defined ISCC goto :noinno

"%ISCC%" installer.iss
if errorlevel 1 goto :fail
echo.
echo ============================================================
echo  DONE. Installer created:
echo  %CD%\installer_output\SeedrMovieManager_Setup.exe
echo ============================================================
goto :end

:noinno
echo.
echo App built OK: %CD%\dist\SeedrMovieManager\SeedrMovieManager.exe
echo Inno Setup 6 was not found, so no installer was made.
echo Install it from https://jrsoftware.org/isdl.php and run this again.
goto :end

:fail
echo.
echo ******** BUILD FAILED - see the messages above ********

:end
echo.
pause
