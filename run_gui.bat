@echo off
setlocal
cd /d "%~dp0"

set PYTHONUTF8=1
set PYTHONUNBUFFERED=1

where pythonw >nul 2>nul
if %ERRORLEVEL% equ 0 (
    start "" pythonw -m rok_automation --gui %*
) else (
    start "" python -m rok_automation --gui %*
)
exit
