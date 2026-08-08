# -*- coding: utf-8 -*-
"""Обновление бота на домашнем компьютере: свежий код, библиотеки, проверки."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
PY = os.path.join(HERE, "venv", "Scripts", "python.exe")
RULER = "=" * 62


def step(title, args, executable=None):
    print(f"\n{RULER}\n  {title}\n{RULER}")
    try:
        return subprocess.call(args, executable=executable)
    except FileNotFoundError:
        print(f"  Не нашёл программу: {args[0]}")
        return 1


if step("Забираю свежий код с GitHub", ["git", "pull"]) != 0:
    print("\n  Не получилось. Обычно помогает повторный запуск: связь рваная.")

if not os.path.exists(PY):
    step("Создаю рабочее окружение, это пара минут", [sys.executable, "-m", "venv", "venv"])

step("Доставляю библиотеки", [PY, "-m", "pip", "install", "-q",
                              "--disable-pip-version-check", "-r", "requirements.txt"])

bad = 0
for name in ("test_avito_monitor.py", "test_avito_bot.py", "test_resale_expert.py"):
    bad += step(f"Проверяю: {name}", [PY, name]) != 0

print("\n" + RULER)
if bad:
    print(f"  Проверки не прошли ({bad} из 3). Покажи этот вывод - разберусь.")
else:
    print("  Всё цело. Можно запускать бота.")
print(RULER)
