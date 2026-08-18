@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
title AI Scientist - http://127.0.0.1:8848/static/index.html

echo [1/2] Checking port 8848...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8848 .*LISTENING"') do (
    if not "%%P"=="0" (
        echo Stopping process %%P using port 8848...
        taskkill /F /PID %%P >nul 2>&1
    )
)

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo Project virtual environment was not found. Falling back to system Python.
    set "PYTHON_EXE=python"
)

rem The batch file opens the browser itself after the page is reachable.
set "AUTO_OPEN_BROWSER=0"
set "TEST_URL=http://127.0.0.1:8848/static/index.html"

echo [2/2] Starting AI Scientist...
echo URL: %TEST_URL%
echo.

start "AI Scientist Server" /min "%PYTHON_EXE%" "%~dp0src\main.py"

echo Waiting for the web service...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$deadline=(Get-Date).AddSeconds(45); while((Get-Date) -lt $deadline) { try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%TEST_URL%'; if($r.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Milliseconds 700 }; exit 1"

if errorlevel 1 (
    echo.
    echo The service did not become ready within 45 seconds.
    echo Check the minimized "AI Scientist Server" window for details.
    pause
    exit /b 1
)

echo Service is ready. Opening browser...
start "" "%TEST_URL%"
endlocal
exit /b 0
