@echo off
title Gmail Job Agent

echo ========================================
echo Starting Gmail Job Agent...
echo ========================================

cd /d %~dp0
cd "C:\Users\shalo\OneDrive\Documents\gmail-job-agent"

REM ---- Activate virtual environment ----
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo Creating virtual environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
)

REM ---- Install requirements ----
echo Installing dependencies...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt

REM ---- Claude key note ----
echo Note: Claude API key will be resolved by Python ^(.env file or system env var^).

REM ---- Run unified scan ----
echo Running email scan...
python 02_scan_jobs.py
if errorlevel 1 (
    echo Scan failed.
    pause
    exit /b 1
)

REM ---- Start dashboard in background ----
echo.
echo Launching Streamlit dashboard...
start "" /min cmd /c "streamlit run 03_dashboard.py --server.headless true"

REM ---- Wait for Streamlit to be ready (poll every 2s) ----
echo Waiting for dashboard to start...
:waitloop
powershell -Command "try { $null = Invoke-WebRequest -Uri 'http://localhost:8501' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto waitloop
)

echo Dashboard is ready!
start http://localhost:8501

pause
