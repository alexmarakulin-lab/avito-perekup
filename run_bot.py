# -*- coding: utf-8 -*-
"""Запуск бота с домашнего компьютера.

Проверяет настройки и объясняет по-русски, чего не хватает. Раньше эти
проверки жили в .bat-файле, но русский текст внутри него Windows читает не
в той кодировке и рассыпает на куски, пытаясь выполнить их как команды.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import env_file

RULER = "=" * 62


def fail(*lines):
    print("\n" + RULER)
    for line in lines:
        print("  " + line)
    print(RULER + "\n")
    sys.exit(1)


if not os.path.exists(os.path.join(HERE, ".env")):
    fail("Нет файла настроек.", "", "Запусти «Настроить бота» - он всё создаст и спросит.")

env_file.load()

if not os.getenv("AVITO_BOT_TOKEN"):
    fail("Не задан токен бота.", "", "Запусти «Настроить бота» и вставь токен от @BotFather.")

if not os.getenv("AVITO_OWNER_ID"):
    print("\n  Внимание: не задан твой номер - боту сможет писать кто угодно.")
    print("  Поправить можно через «Настроить бота».\n")

print(RULER)
print("  ПЕРЕКУП 23 - бот запускается")
print(RULER)
print("  Это окно закрывать нельзя: пока оно открыто, бот работает.")
print("  Остановить - Ctrl+C или закрыть окно.")
print(RULER + "\n")

import avito_bot

avito_bot.build_app().run_polling(drop_pending_updates=True, allowed_updates=["message"])
