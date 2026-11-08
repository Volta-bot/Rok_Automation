@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1

cd /d "%~dp0"

if "%~1"=="" (
    echo Starting Rise of Kingdoms Farming Automation...
    python -m rok_automation --farm
) else (
    python -m rok_automation %*
)

echo.
pause
