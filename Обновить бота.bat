@echo off
chcp 65001 >nul
title Obnovlenie bota
cd /d "%~dp0"
python update_bot.py
echo.
pause
