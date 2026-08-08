@echo off
chcp 65001 >nul
title Перекуп 23 - бот работает
cd /d "%~dp0"

if not exist ".env" (
    echo.
    echo   Нет файла настроек .env
    echo   Скопируй .env.example в .env и впиши токен бота.
    echo.
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo.
    echo   Нет рабочего окружения. Запусти "Обновить бота.bat"
    echo.
    pause
    exit /b 1
)

echo.
echo   Бот запускается. Это окно закрывать нельзя - пока оно открыто,
echo   бот работает. Остановить: Ctrl+C или просто закрыть окно.
echo.

venv\Scripts\python.exe avito_bot.py

echo.
echo   Бот остановился. Если это неожиданно - причина в строках выше.
echo.
pause
