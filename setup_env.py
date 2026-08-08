# -*- coding: utf-8 -*-
"""Вопросник для первичной настройки: спрашивает и сам вписывает в .env.

Правка .env Блокнотом на Windows подводит: файл без расширения он норовит
сохранить не туда или не сохранить вовсе, и это молча. Здесь пользователь
только отвечает на вопросы, а в файл пишет программа.
"""
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, ".env")
SAMPLE = os.path.join(HERE, ".env.example")


def ask_token(current: str) -> str:
    print("\n1. ТОКЕН БОТА")
    print("   Это пароль бота. Выдаёт @BotFather командой /mybots,")
    print("   а новый - командой /revoke. Выглядит так:")
    print("   8649312428:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    if current:
        print(f"   Сейчас вписан токен бота номер {current.split(':')[0]}.")
        print("   Нажми Enter, чтобы оставить его.")
    while True:
        value = input("\n   Токен: ").strip()
        if not value and current:
            return current
        if ":" not in value or not value.split(":")[0].isdigit():
            print("   Это не похоже на токен: нужны цифры, двоеточие, потом буквы.")
            continue
        if len(value) < 40:
            print(f"   Коротковато: {len(value)} знаков вместо примерно 45. Скопировался целиком?")
            continue
        return value


def ask_id(current: str) -> str:
    print("\n2. ТВОЙ НОМЕР В TELEGRAM")
    print("   Это не токен, а короткое число из 9-10 цифр.")
    print("   Узнать: найди в Telegram бота @userinfobot и нажми «Запустить».")
    if current:
        print(f"   Сейчас вписан {current}. Нажми Enter, чтобы оставить.")
    while True:
        value = input("\n   Номер: ").strip()
        if not value and current:
            return current
        if not value.isdigit():
            print("   Нужны только цифры, без пробелов и букв.")
            continue
        return value


def put(text: str, key: str, value: str) -> str:
    """Вписывает значение в строку key=... , не трогая остальной файл."""
    line = f"{key}={value}"
    if re.search(rf"^{key}=.*$", text, re.M):
        return re.sub(rf"^{key}=.*$", line, text, count=1, flags=re.M)
    return text.rstrip("\n") + f"\n{line}\n"


def current_value(text: str, key: str) -> str:
    match = re.search(rf"^{key}=(.*)$", text, re.M)
    return match.group(1).strip() if match else ""


def main():
    if not os.path.exists(ENV):
        shutil.copyfile(SAMPLE, ENV)
        print("Создан файл настроек .env")

    with open(ENV, encoding="utf-8") as f:
        text = f.read()

    print("=" * 60)
    print("  НАСТРОЙКА БОТА-ПЕРЕКУПА")
    print("=" * 60)

    token = ask_token(current_value(text, "AVITO_BOT_TOKEN"))
    owner = ask_id(current_value(text, "AVITO_OWNER_ID"))

    text = put(text, "AVITO_BOT_TOKEN", token)
    text = put(text, "AVITO_OWNER_ID", owner)
    text = put(text, "AVITO_OWNER_CHAT", owner)

    with open(ENV, "w", encoding="utf-8") as f:
        f.write(text)

    print("\n" + "=" * 60)
    print(f"  Записано. Бот номер {token.split(':')[0]}, хозяин {owner}.")
    print("  Теперь запускай «Запустить бота».")
    print("=" * 60)


if __name__ == "__main__":
    main()
