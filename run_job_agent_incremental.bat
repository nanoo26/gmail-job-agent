@echo off
cd /d "C:\Users\shalo\OneDrive\Documents\gmail-job-agent"
set "SCAN_ONLY_NEW=1"
set "SCAN_LIMIT=75"
set "SCAN_MAX_RESULTS=160"
set "CLAUDE_MAX_PER_RUN=100"
call run_job_agent.bat
