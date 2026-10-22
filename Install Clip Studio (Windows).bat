@echo off
rem Double-click to set up Clip Studio on Windows. One time, about 20 minutes (mostly downloading).
rem Safe to run again: anything already installed is skipped.
setlocal
cd /d "%~dp0"
title Clip Studio setup
echo Clip Studio setup
echo This installs what Clip Studio needs. Keep this window open until it says Done.
echo.

where winget >nul 2>nul
if errorlevel 1 (
  echo Windows' app installer ^(winget^) wasn't found. Install "App Installer" from the Microsoft Store, then double-click Install again.
  goto :failed
)

echo ==^> Installing Python and FFmpeg ^(skipped if you already have them^)
py -3.12 --version >nul 2>nul
if errorlevel 1 winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
where ffmpeg >nul 2>nul
if errorlevel 1 winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements

rem find the Python that was just installed (a new install isn't on this window's PATH yet)
set "PYEXE="
for /f "delims=" %%i in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%ProgramFiles%\Python312\python.exe" set "PYEXE=%ProgramFiles%\Python312\python.exe"
if not defined PYEXE (
  echo Python 3.12 wasn't found after installing it. Restart the computer and double-click Install again.
  goto :failed
)

echo.
echo ==^> Setting up Clip Studio
if not exist ".venv\Scripts\python.exe" (
  "%PYEXE%" -m venv .venv
  if errorlevel 1 ( echo Couldn't create Clip Studio's Python folder. & goto :failed )
)
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 ( echo Some of Clip Studio's parts didn't install. & goto :failed )

echo.
echo ==^> Downloading the speech, face and photo models ^(about 1.2 GB^)
".venv\Scripts\python.exe" scripts\get_models.py
if errorlevel 1 ( echo A model didn't download. Check the internet connection and double-click Install again. & goto :failed )

echo.
echo ==^> Done
echo Double-click "Open Clip Studio (Windows)" to start it.
echo The first time, add a Claude API key in Clip Studio's Settings.
pause
exit /b 0

:failed
echo.
echo Setup stopped. Take a screenshot of this window and send it to Elijah.
pause
exit /b 1
