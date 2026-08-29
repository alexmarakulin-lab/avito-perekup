@echo off
chcp 65001 >nul
title Nastroyka bota
cd /d "%~dp0"
venv\Scripts\python.exe setup_env.py
echo.
pause
