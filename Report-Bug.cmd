@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  py -3 -I tools\report_bug.py
) else (
  python -I tools\report_bug.py
)
pause
