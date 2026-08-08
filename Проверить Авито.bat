@echo off
chcp 65001 >nul
title Проверка Авито
cd /d "%~dp0"

echo.
echo   Проверяю, читается ли выдача Авито. До полуминуты.
echo.

venv\Scripts\python.exe check_avito.py %*

echo.
pause
