@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

rem === Entry guard: prevent window from closing on double-click ===
rem When Explorer double-clicks a .bat, it uses cmd.exe /C which closes the window on exit.
rem Detect this and self-restart with cmd /K to keep the window open for error inspection.
set "_BAT=%~f0"
set "_IS_DOUBLE_CLICK=0"
echo %CMDCMDLINE% | findstr /I /C:" /c " | findstr /I /C:"%_BAT%" >nul 2>&1
if not errorlevel 1 set "_IS_DOUBLE_CLICK=1"
if "%_IS_DOUBLE_CLICK%"=="1" (
    start "AI Scientist Launcher" "%COMSPEC%" /K """%_BAT%"" %*"
    exit /b 0
)

cd /d "%~dp0"
title AI Scientist - http://127.0.0.1:8848/static/index.html

echo [1/2] Checking port 8848...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8848 .*LISTENING"') do (
    if not "%%P"=="0" (
        echo Stopping process %%P using port 8848...
        taskkill /F /PID %%P >nul 2>&1
    )
)

rem Support both .venv and venv directory names
set "PYTHON_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%~dp0venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
if not defined PYTHON_EXE (
    echo Project virtual environment was not found. Falling back to system Python.
    set "PYTHON_EXE=python"
)

set "AUTO_OPEN_BROWSER=0"
set "TEST_URL=http://127.0.0.1:8848/static/index.html"

set "BOOT_LOG=%~dp0data\boot.log"
if not exist "%~dp0data" mkdir "%~dp0data"
del /q "%BOOT_LOG%" >nul 2>&1

echo [2/2] Starting AI Scientist...
echo URL: %TEST_URL%
echo (Launch log: %BOOT_LOG%)
echo.

rem Start backend service via _boot_runner.py (tee: stdout/stderr to both console and boot.log)
rem Using cmd /k keeps the server window open on crash so you can see the Traceback.
set "RUNNER_PY=%~dp0scripts\_boot_runner.py"
set "_SRV_CMD="%PYTHON_EXE%" "%RUNNER_PY%" "%BOOT_LOG%" "%PYTHON_EXE%" "%~dp0src\main.py""
start "AI Scientist Server" /NORMAL "%COMSPEC%" /S /K %_SRV_CMD%

echo Waiting for the web service...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$deadline=(Get-Date).AddSeconds(45); while((Get-Date) -lt $deadline) { try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%TEST_URL%'; if($r.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Milliseconds 700 }; exit 1"

if errorlevel 1 (
    echo.
    echo The service did not become ready within 45 seconds.
    echo.
    echo ====== Last lines of boot log ======
    if exist "%BOOT_LOG%" (
        powershell.exe -NoProfile -Command "Get-Content '%BOOT_LOG%' -Tail 40"
    ) else (
        echo (no boot log found — server process may not have started at all)
    )
    echo ===================================
    echo.
    echo Opening full boot log in notepad...
    if exist "%BOOT_LOG%" ( start notepad "%BOOT_LOG%" )
    pause
    exit /b 1
)

echo Service is ready. Opening browser...
start "" "%TEST_URL%"
endlocal
exit /b 0
