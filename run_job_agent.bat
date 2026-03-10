@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Gmail Job Agent
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"

echo ========================================
echo Starting Gmail Job Agent...
echo ========================================

cd C:\Users\shalo\OneDrive\Documents\gmail-job-agent

if not exist "02_scan_jobs.py" (
    echo ERROR: 02_scan_jobs.py not found in:
    echo   %CD%
    pause
    exit /b 1
)

REM ---- Activate virtual environment ----
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv >nul 2>&1
    if errorlevel 1 (
        python -m venv .venv
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Could not create or find .venv\Scripts\python.exe
    pause
    exit /b 1
)
set "PYTHON_EXE=.venv\Scripts\python.exe"

call .venv\Scripts\activate.bat

REM ---- Gmail auth bootstrap ----
if not exist "token.json" (
    echo token.json not found. Running Gmail authentication...
    "%PYTHON_EXE%" 01_auth.py
    if errorlevel 1 (
        echo ERROR: Gmail authentication failed.
        pause
        exit /b 1
    )
)

REM ---- Install requirements ----
echo Installing dependencies...
"%PYTHON_EXE%" -m pip install --upgrade pip >nul 2>&1
"%PYTHON_EXE%" -m pip install -r requirements.txt

REM ---- Claude key note ----
echo Note: Claude API key will be resolved by Python ^(.env file or system env var^).

REM ---- Run unified scan ----
set "SCAN_FAILED=0"
set "SCAN_LIMIT=100"
set "SCAN_FROM_YEAR_START=1"
set "CLAUDE_DEBUG_STOP_EARLY=0"
set "CLAUDE_MAX_PER_RUN=100"
echo Running email scan...
"%PYTHON_EXE%" 02_scan_jobs.py
if errorlevel 1 (
    set "SCAN_FAILED=1"
    echo.
    echo WARNING: Email scan failed.
    echo Most common causes:
    echo   1^) Internet/firewall blocked Google OAuth access
    echo   2^) Expired token.json ^(run: "%PYTHON_EXE%" 01_auth.py^)
    echo   3^) Gmail API permissions/config issue
    if not exist "job_emails.csv" (
        echo ERROR: job_emails.csv not found, so dashboard has no data to show.
        pause
        exit /b 1
    )
    echo Continuing with existing job_emails.csv...
)

REM ---- Start dashboard in background ----
echo.
echo Launching Streamlit dashboard...
start "" /min "%PYTHON_EXE%" -m streamlit run 03_dashboard.py --server.headless true

REM ---- Wait for Streamlit to be ready (poll every 2s) ----
echo Waiting for dashboard to start...
set /a tries=0
:waitloop
powershell -Command "try { $null = Invoke-WebRequest -Uri 'http://localhost:8501' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    set /a tries+=1
    if !tries! GEQ 90 (
        echo Dashboard did not start in time ^(180s^).
        pause
        exit /b 1
    )
    timeout /t 2 /nobreak >nul
    goto waitloop
)

echo Dashboard is ready!
start http://localhost:8501

if "!SCAN_FAILED!"=="1" (
    echo NOTE: Dashboard opened with existing data because scan failed.
)

pause
