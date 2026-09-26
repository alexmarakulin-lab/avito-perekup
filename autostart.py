# -*- coding: utf-8 -*-
"""Автозапуск бота вместе с Windows.

Как это устроено. У Windows есть папка «Автозагрузка»: всё, что в ней
лежит, запускается при входе в систему. Мы кладём туда крошечный файл,
который поднимает бота свёрнутым окном. Ни прав администратора, ни
служб, ни планировщика заданий - и убирается так же просто.

Почему именно папка, а не служба Windows. Боту нужен рабочий стол:
браузер должен открыть окно, пусть и свёрнутое. Служба работает без
рабочего стола, и браузер в ней не поднимется - ровно та беда, из-за
которой не вышло с сервером.

Запуск: «Автозапуск.bat» или python autostart.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "venv", "Scripts", "python.exe")
STARTUP = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                       "Start Menu", "Programs", "Startup")
LINK = os.path.join(STARTUP, "perekup-bot.bat")

RULER = "=" * 62


def short(path: str) -> str:
    """Короткое имя пути в стиле C:\\Users\\8523~1 - без букв кириллицы.

    Папка пользователя здесь называется «Александр», и путь к ней целиком
    состоит из русских букв. Файл .bat Windows читает не в той кодировке,
    в которой его пишет Python, и русский путь в нём превращается в мусор -
    на этом мы уже обжигались с кнопками. Короткое имя, которое Windows
    держит для каждой папки, состоит из одних латинских букв и цифр, и
    кодировка ему безразлична.
    """
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    got = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
    value = buf.value if got else path
    return value if value.isascii() else path


def content() -> str:
    return (f'@echo off\r\n'
            f'cd /d "{short(HERE)}"\r\n'
            f'start "" /min "{short(PY)}" run_bot.py\r\n')


def enabled() -> bool:
    return os.path.exists(LINK)


def turn_on():
    if not os.path.isdir(STARTUP):
        print(f"  Не нашёл папку автозагрузки:\n  {STARTUP}")
        return
    body = content()
    if not body.isascii():
        # Короткого имени не нашлось - пишем в той кодировке, в которой
        # Windows читает .bat, иначе путь превратится в мусор.
        with open(LINK, "w", encoding="cp866", errors="replace", newline="") as f:
            f.write(body)
    else:
        with open(LINK, "w", encoding="ascii", newline="") as f:
            f.write(body)
    print("  Включено. Теперь бот будет подниматься сам при включении")
    print("  компьютера - свёрнутым окном, мешать не будет.")


def turn_off():
    try:
        os.remove(LINK)
        print("  Выключено. Запускать придётся вручную.")
    except FileNotFoundError:
        print("  Автозапуск и так не был включён.")


def main():
    print(RULER)
    print("  АВТОЗАПУСК БОТА ВМЕСТЕ С WINDOWS")
    print(RULER)

    if not os.path.exists(PY):
        print("\n  Не нашёл рабочее окружение. Сначала запусти «Обновить бота».")
        return

    print(f"\n  Сейчас: {'ВКЛЮЧЁН' if enabled() else 'выключен'}")
    print("\n  1 - включить автозапуск")
    print("  2 - выключить автозапуск")
    print("  Enter - ничего не менять")

    choice = input("\n  Твой выбор: ").strip()
    print()
    if choice == "1":
        turn_on()
    elif choice == "2":
        turn_off()
    else:
        print("  Ничего не менял.")

    print()
    print(RULER)
    print("  Важно: пока компьютер спит, бот не ищет. Автозапуск этого не")
    print("  лечит - он поднимает бота при включении, а не будит машину.")
    print("  Отключить сон: Параметры - Система - Питание -")
    print("  «При питании от сети переводить в спящий режим» - Никогда.")
    print(RULER)


if __name__ == "__main__":
    sys.exit(main() or 0)
