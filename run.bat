@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1

cd /d "%~dp0"

:prompt
set "CONFIRM="
set /p "CONFIRM=Enter y to run Rise of Kingdoms Farming Automation: "
if /i not "%CONFIRM%"=="y" (
    echo Cancelled.
    exit /b 0
)

if "%~1"=="" (
    echo Starting Rise of Kingdoms Farming Automation...
    python -m rok_automation --farm
) else (
    python -m rok_automation %*
)

echo.
goto prompt
