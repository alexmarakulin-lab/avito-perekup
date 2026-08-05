# -*- coding: utf-8 -*-
"""Проверка Авито из консоли, без участия Telegram.

Раньше единственным способом проверить разбор выдачи была кнопка в боте.
Но кнопка работает через опрос Telegram - самое ненадёжное место на этом
сервере. Получалось, что исправную часть проверяем через сломанную.

Здесь то же самое, но напрямую:

    docker compose exec avito python check_avito.py
    docker compose exec avito python check_avito.py "сплит система"
"""
import asyncio
import sys

import avito_monitor


async def main():
    query = " ".join(sys.argv[1:]) or "перфоратор"
    print(await avito_monitor.self_test(query))


if __name__ == "__main__":
    asyncio.run(main())
