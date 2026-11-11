@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1

cd /d "%~dp0"

echo Starting ROKauto GUI...
python -m rok_automation --gui %*
