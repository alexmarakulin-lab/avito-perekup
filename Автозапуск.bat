@echo off
chcp 65001 >nul
title Avtozapusk bota
cd /d "%~dp0"
venv\Scripts\python.exe autostart.py
echo.
pause
