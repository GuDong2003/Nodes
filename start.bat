@echo off
chcp 65001 >nul
title ProxyScrape Auto Register
cd /d "%~dp0"
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PY=python"
where %PY% >nul 2>&1 || (where py >nul 2>&1 && set "PY=py")
"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    pause
    exit /b 1
)

:menu
cls
echo.
echo ============================================================
echo            ProxyScrape Auto Register
echo ============================================================
echo.

set "COUNT=5"
set /p "COUNT=  1. Count (how many keys)   [5]: "

set "THREADS=3"
set /p "THREADS=  2. Threads (concurrency)   [3]: "

set "HIDE=Y"
set /p "HIDE=  3. Hide browser window   [Y/n]: "

if "%COUNT%"=="" set "COUNT=5"
if "%THREADS%"=="" set "THREADS=3"
if "%HIDE%"=="" set "HIDE=Y"

echo.
echo ============================================================
echo   count=%COUNT%   threads=%THREADS%   hide=%HIDE%
echo ============================================================
echo   Starting in 2s ... (Ctrl+C to abort)
ping -n 3 127.0.0.1 >nul

REM pipe 4 lines to script: count / threads / hide / 0(exit menu loop)
(echo %COUNT%& echo %THREADS%& echo %HIDE%& echo 0) | "%PY%" "%~dp0proxyscrape_register.py"

echo.
echo ============================================================
echo   Round finished. Press any key for next round,
echo   or close this window to stop.
echo ============================================================
pause >nul
goto menu
