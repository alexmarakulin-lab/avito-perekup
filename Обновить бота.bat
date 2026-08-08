@echo off
chcp 65001 >nul
title Обновление бота
cd /d "%~dp0"

echo.
echo   Забираю свежий код с GitHub...
echo.
git pull

if not exist "venv\Scripts\python.exe" (
    echo.
    echo   Создаю рабочее окружение, это займёт пару минут...
    python -m venv venv
)

echo.
echo   Доставляю библиотеки...
venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt

echo.
echo   Проверяю, что всё цело...
venv\Scripts\python.exe test_avito_monitor.py
venv\Scripts\python.exe test_avito_bot.py

echo.
echo   Готово. Если выше два раза написано ВСЁ ЗЕЛЁНОЕ - можно запускать бота.
echo.
pause
