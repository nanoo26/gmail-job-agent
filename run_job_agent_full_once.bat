@echo off
cd /d "C:\Users\shalo\OneDrive\Documents\gmail-job-agent"

REM Full one-time rebuild:
REM - Scan all messages in window (not only new)
REM - No practical cap on scan/Claude within the fetched set
set "SCAN_ONLY_NEW=0"
set "SCAN_LIMIT=0"
set "SCAN_MAX_RESULTS=0"
set "SCAN_FROM_YEAR_START=1"
set "CLAUDE_DEBUG_STOP_EARLY=0"
set "CLAUDE_MAX_PER_RUN=0"

call run_job_agent.bat
