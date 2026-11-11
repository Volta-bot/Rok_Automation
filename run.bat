@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1

cd /d "%~dp0"

if not "%~1"=="" (
    python -m rok_automation %*
    exit /b %ERRORLEVEL%
)

:menu
echo ========================================================
echo   Rise of Kingdoms Automation (ROKauto)
echo ========================================================
echo  [1] Launch Graphical User Interface (Recommended)
echo  [2] Run CLI Resource Farming (--farm)
echo  [Q] Quit
echo ========================================================
set "CHOICE="
set /p "CHOICE=Select an option [1/2/Q] (default: 1): "

if /i "%CHOICE%"=="q" exit /b 0
if /i "%CHOICE%"=="2" goto run_cli
if /i "%CHOICE%"=="y" goto run_cli

:run_gui
echo Starting Graphical User Interface...
where pythonw >nul 2>nul
if %ERRORLEVEL% equ 0 (
    start "" pythonw -m rok_automation --gui
) else (
    start "" python -m rok_automation --gui
)
exit /b 0

:run_cli
echo Starting Rise of Kingdoms Farming Automation...
python -m rok_automation --farm
echo.
goto menu
