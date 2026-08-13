# -*- coding: utf-8 -*-
"""Вопросник для первичной настройки: спрашивает и сам вписывает в .env.

Правка .env Блокнотом на Windows подводит: файл без расширения он норовит
сохранить не туда или не сохранить вовсе, и это молча. Здесь пользователь
только отвечает на вопросы, а в файл пишет программа.
"""
import getpass
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, ".env")
SAMPLE = os.path.join(HERE, ".env.example")


def secret_input(prompt: str) -> str:
    """Спрашивает секрет, не печатая его на экран.

    Прежде вопросник показывал вписанное как обычный текст, и оно тут же
    попадало на скриншот: так утёк токен бота (трижды) и ключ Groq. Пароль
    надёжен ровно до первого снимка экрана.

    Буквы при вводе не видны совсем - ни точек, ни звёздочек. Так устроены
    все пароли в командной строке, и это сбивает с толку, поэтому про это
    сказано прямо в подсказке. Вставка правой кнопкой мыши работает как
    обычно, просто вставленное остаётся невидимым.
    """
    try:
        return getpass.getpass(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception:
        # Редкие консоли скрытый ввод не поддерживают. Остаться совсем без
        # настройки хуже, чем показать строку, - но предупредим об этом.
        print("   (эта консоль не умеет прятать ввод, строка будет видна)")
        return input(prompt).strip()


def ask_token(current: str) -> str:
    print("\n1. ТОКЕН БОТА")
    print("   Это пароль бота. Выдаёт @BotFather командой /mybots,")
    print("   а новый - командой /revoke. Выглядит так:")
    print("   8649312428:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    if current:
        print(f"   Сейчас вписан токен бота номер {current.split(':')[0]}.")
        print("   Нажми Enter, чтобы оставить его.")
    print("   Набранное показано не будет - так и задумано.")
    while True:
        value = secret_input("\n   Токен: ")
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


def ask_groq(current: str) -> str:
    print("\n3. КЛЮЧ ДЛЯ КОНСУЛЬТАНТА (можно пропустить)")
    print("   Без него монитор работает, но бот не отвечает на вопросы")
    print("   вроде «перфоратор за 3500, брать?».")
    print("   Бесплатно на console.groq.com, ключ начинается с gsk_")
    print("   Нажми Enter, чтобы пропустить.")
    if current:
        print("   Сейчас ключ уже вписан. Enter - оставить как есть.")
    print("   Набранное показано не будет - так и задумано.")
    while True:
        value = secret_input("\n   Ключ: ")
        if not value:
            return current
        if not value.startswith("gsk_"):
            print("   Ключи Groq начинаются с gsk_. Скопировался целиком?")
            continue
        return value


def ask_proxy(current: str) -> str:
    print("\n4. ПРОКСИ ДЛЯ АВИТО (можно пропустить)")
    print("   Нужен, если Авито отвечает капчей. Вид строки:")
    print("   http://логин:пароль@адрес:порт")
    print("   Нажми Enter, чтобы пропустить.")
    if current:
        print("   Сейчас прокси уже вписан. Enter - оставить как есть.")
    # В строке прокси пароль идёт прямо внутри адреса, так что она такой же
    # секрет, как токен.
    print("   Набранное показано не будет - так и задумано.")
    while True:
        value = secret_input("\n   Прокси: ")
        if not value:
            return current
        if "://" not in value:
            print("   Не хватает начала: http:// или socks5://")
            continue
        if "@" in value and ":" not in value.split("@", 1)[1]:
            print("   После адреса нужен порт через двоеточие.")
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
    groq = ask_groq(current_value(text, "GROQ_API_KEY"))
    proxy = ask_proxy(current_value(text, "AVITO_PROXY"))

    text = put(text, "AVITO_BOT_TOKEN", token)
    text = put(text, "AVITO_OWNER_ID", owner)
    text = put(text, "AVITO_OWNER_CHAT", owner)
    text = put(text, "GROQ_API_KEY", groq)
    text = put(text, "AVITO_PROXY", proxy)

    with open(ENV, "w", encoding="utf-8") as f:
        f.write(text)

    print("\n" + "=" * 60)
    print(f"  Записано. Бот номер {token.split(':')[0]}, хозяин {owner}.")
    print(f"  Консультант: {'включён' if groq else 'выключен, ключа нет'}")
    # Пароль от прокси не показываем: он уже был бы на скриншоте.
    print(f"  Авито: {'через прокси' if proxy else 'напрямую, прокси нет'}")
    print("  Теперь запускай «Запустить бота».")
    print("=" * 60)


if __name__ == "__main__":
    main()
