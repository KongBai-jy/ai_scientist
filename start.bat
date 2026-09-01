@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1

rem =====================================================================
rem  AI Scientist - one-click launcher
rem
rem  This file MUST stay: ASCII text / UTF-8 WITHOUT BOM / CRLF endings.
rem  cmd.exe mis-parses .bat files that carry a BOM or bare LF endings:
rem  "^" line continuations and multi-line ( ) blocks fail silently,
rem  which is exactly how this script used to end up never opening
rem  the browser.
rem =====================================================================

set "SRV_PORT=8848"
set "TEST_URL=http://127.0.0.1:%SRV_PORT%/static/index.html"
set "WAIT_SEC=120"
set "BOOT_LOG=%~dp0data\boot.log"

rem --- src/main.py:_open_browser also opens a tab; this var is inherited by the
rem --- server window, so disable it there - this script opens the page exactly once.
set "AUTO_OPEN_BROWSER=0"

rem --- Explorer double-click uses "cmd /c", which closes the window on exit.
rem --- Relaunch ourselves under "cmd /k call" so a crash stays readable.
set "_BAT=%~f0"
echo(%CMDCMDLINE% | findstr /I /C:" /c " | findstr /I /C:"%~nx0" >nul 2>&1
if "%errorlevel%"=="0" (
    start "AI Scientist Launcher" /D "%~dp0." "%ComSpec%" /K call "%_BAT%" %*
    exit /b 0
)

cd /d "%~dp0"
title AI Scientist - %TEST_URL%

echo [1/3] Freeing port %SRV_PORT%...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%SRV_PORT% .*LISTENING"') do (
    if not "%%P"=="0" (
        echo        stopping PID %%P that holds port %SRV_PORT%
        taskkill /F /PID %%P >nul 2>&1
    )
)

echo [2/3] Checking Python environment...
set "PYTHON_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%~dp0venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
if not defined PYTHON_EXE (
    echo        no virtualenv found, falling back to system python
    set "PYTHON_EXE=python"
)
"%PYTHON_EXE%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: "%PYTHON_EXE%" cannot import fastapi or uvicorn.
    echo        Fix the environment first:
    echo            "%PYTHON_EXE%" -m pip install -r requirements.txt
    echo.
    pause
    exit /b 2
)
echo        %PYTHON_EXE%

echo [3/3] Starting the backend service...
echo        URL: %TEST_URL%
echo        log: %BOOT_LOG%
if not exist "%~dp0data" mkdir "%~dp0data"
del /q "%BOOT_LOG%" >nul 2>&1
set "RUNNER_PY=%~dp0scripts\_boot_runner.py"
set "MAIN_PY=%~dp0src\main.py"
start "AI Scientist Server" /D "%~dp0." /NORMAL "%ComSpec%" /K call "%PYTHON_EXE%" "%RUNNER_PY%" "%BOOT_LOG%" "%PYTHON_EXE%" "%MAIN_PY%"

rem --- Single-line PowerShell poll: no "^" continuation, so LF/CRLF both safe.
echo        waiting for the service (max %WAIT_SEC% s)...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$d=(Get-Date).AddSeconds(%WAIT_SEC%); while ((Get-Date) -lt $d) { try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 '%TEST_URL%'; if ($r.StatusCode -eq 200) { exit 0 } } catch { }; Start-Sleep -Milliseconds 700 }; exit 1"

set "SERVER_UP=1"
if not "%errorlevel%"=="0" set "SERVER_UP=0"
if "%SERVER_UP%"=="1" goto open_browser

echo.
echo WARNING: %TEST_URL% did not answer within %WAIT_SEC% seconds.
echo          ===== last 40 lines of boot.log =====
if not exist "%BOOT_LOG%" goto no_log
powershell.exe -NoProfile -Command "Get-Content -LiteralPath '%BOOT_LOG%' -Tail 40 -Encoding UTF8"
goto log_done
:no_log
echo          boot.log is missing - the server process never started at all.
:log_done
echo          ======================================
echo          Check the "AI Scientist Server" window for the traceback.
echo.

:open_browser
echo Opening browser: %TEST_URL%
start "" "%TEST_URL%"
if "%SERVER_UP%"=="1" exit /b 0
echo.
echo Press any key to close this launcher window...
pause >nul
exit /b 1
