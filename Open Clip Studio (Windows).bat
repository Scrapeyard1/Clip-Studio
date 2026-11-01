@echo off
rem Double-click to start Clip Studio. It opens in your web browser.
rem Keep this window open while you use it; closing it stops Clip Studio.
setlocal
cd /d "%~dp0"
title Clip Studio
if not exist ".venv\Scripts\python.exe" (
  echo Clip Studio isn't set up yet. Double-click "Install Clip Studio (Windows)" first.
  pause
  exit /b 1
)

rem already running (for example, opened twice)? just show it
curl -fs http://localhost:8765/api/health >nul 2>nul
if not errorlevel 1 (
  start "" http://localhost:8765
  echo Clip Studio was already running. It's open in your browser.
  timeout /t 3 >nul
  exit /b 0
)

echo Starting Clip Studio... it opens in your browser when it's ready.
echo Keep this window open while you use it. Close it to stop Clip Studio.
rem Refresh & update restarts it with exit code 3. The whole block is read before it runs,
rem so the update can replace this file safely while Clip Studio is open.
set CLIPSTUDIO_LAUNCHER=1
:run
(
  ".venv\Scripts\python.exe" app\server.py
  if errorlevel 3 if not errorlevel 4 (
    echo.
    echo Updated. Starting the new version...
    set NO_BROWSER=1
    goto run
  )
  echo.
  echo Clip Studio stopped. If that wasn't on purpose, take a screenshot of this window and send it to Elijah.
  pause
  exit /b
)
