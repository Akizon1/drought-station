@echo off
setlocal EnableExtensions
title Drought - building the installer
cd /d "%~dp0"

echo ============================================================
echo   Building the Drought installer (Drought_Setup.exe)
echo   Requires: Python 3.10+ and an internet connection
echo ============================================================
echo.

rem --- 1. Python ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python not found. Install it from https://www.python.org/downloads/
  echo         and tick "Add Python to PATH", then run this file again.
  goto :fail
)

rem --- 2. Separate build environment ---
if not exist ".build_venv\Scripts\python.exe" (
  echo [1/4] Creating the build environment...
  %PY% -m venv .build_venv || goto :fail
)
set "VPY=.build_venv\Scripts\python.exe"

echo [2/4] Installing libraries and PyInstaller...
"%VPY%" -m pip install --upgrade pip >nul
"%VPY%" -m pip install -r requirements.txt pyinstaller || goto :fail

rem --- 3. Program without a console window: dist\Drought\Drought.exe ---
echo [3/4] Building Drought.exe - this takes a few minutes...
"%VPY%" -m PyInstaller --noconfirm --clean --windowed --name Drought --icon icon.ico ^
  --collect-all customtkinter --collect-all matplotlib --collect-data tkinter ^
  --add-data "LICENSE.txt;." --add-data "icon.ico;." --add-data "icon.png;." main.py || goto :fail
if not exist "dist\Drought\Drought.exe" goto :fail

rem --- 4. Installer with Inno Setup 6 ---
call :find_iscc
if not defined ISCC (
  echo.
  echo Inno Setup 6 was not found - it packs the program into one installer.
  if /I "%~1"=="/auto" (
    call :install_inno
    goto :after_inno
  )
  choice /C YN /M "Install Inno Setup automatically with winget"
  if errorlevel 2 (
    echo Install Inno Setup 6 from https://jrsoftware.org/isdl.php and run this file again.
    goto :fail
  )
  call :install_inno
)
:after_inno
if not defined ISCC (
  echo [ERROR] Inno Setup still not found. Install it from https://jrsoftware.org/isdl.php
  goto :fail
)

echo [4/4] Packing the installer...
"%ISCC%" installer.iss || goto :fail

echo.
echo ============================================================
echo   DONE: installer_output\Drought_Setup.exe
echo ============================================================
start "" explorer "%~dp0installer_output"
if /I not "%~1"=="/auto" pause
exit /b 0

:install_inno
echo Installing Inno Setup 6 with winget...
winget install -e --id JRSoftware.InnoSetup --scope user --accept-package-agreements --accept-source-agreements
call :find_iscc
if defined ISCC exit /b 0
winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements
call :find_iscc
exit /b 0

:find_iscc
set "ISCC="
for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do (
  if exist "%%~P" set "ISCC=%%~P"
)
exit /b 0

:fail
echo.
echo BUILD FAILED. See the messages above.
if /I not "%~1"=="/auto" pause
exit /b 1
