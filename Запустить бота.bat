@echo off
chcp 65001 >nul
title Perekup 23
cd /d "%~dp0"
venv\Scripts\python.exe run_bot.py
echo.
pause
