@echo off
chcp 65001 >nul
title Proverka Avito
cd /d "%~dp0"
venv\Scripts\python.exe check_avito.py %*
echo.
pause
