# -*- coding: utf-8 -*-
"""Проверка Авито из консоли, без участия Telegram.

Раньше единственным способом проверить разбор выдачи была кнопка в боте.
Но кнопка работает через опрос Telegram - самое ненадёжное место на этом
сервере. Получалось, что исправную часть проверяем через сломанную.

Здесь то же самое, но напрямую:

    docker compose exec avito python check_avito.py
    docker compose exec avito python check_avito.py "сплит система"

Второй режим - подбор заголовков. Нужен, когда Авито отдаёт капчу боту,
но при этом пускает curl с того же сервера. Значит забракован не адрес,
а то, как именно бот представляется. Режим перебирает наборы заголовков
и показывает, с каким из них выдача приходит:

    docker compose exec avito python check_avito.py probe
"""
import asyncio
import sys

import httpx

import avito_monitor

CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Наборы заголовков от самого скромного к самому «браузерному». Смысл в том,
# чтобы найти границу: где Авито ещё пускает, а где уже считает роботом.
VARIANTS = [
    ("как curl: короткий UA и больше ничего",
     {"User-Agent": "Mozilla/5.0 Chrome/126"}),
    ("полный UA Chrome и больше ничего",
     {"User-Agent": CHROME_UA}),
    ("UA Chrome + язык + Accept",
     {"User-Agent": CHROME_UA,
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "ru-RU,ru;q=0.9"}),
    ("как сейчас в боте: весь набор Sec-Fetch",
     avito_monitor._headers()),
]


async def probe():
    url = avito_monitor.build_search_url("перфоратор")
    print(f"Проверяю наборы заголовков на {url[:60]}...\n")

    for name, headers in VARIANTS:
        try:
            async with httpx.AsyncClient(headers=headers, timeout=30,
                                         follow_redirects=True) as client:
                resp = await client.get(url)
            low = resp.text.lower()
            wall = "доступ ограничен" in low or "подтвердите, что вы не робот" in low
            verdict = "КАПЧА" if wall else ("выдача" if resp.status_code == 200 else "?")
            print(f"  HTTP {resp.status_code}  {len(resp.text):>7} симв.  {verdict:6}  {name}")
        except Exception as exc:
            print(f"  ошибка {type(exc).__name__}: {exc}  <- {name}")
        # Пауза между попытками: подряд без передышки Авито закроется от всех.
        await asyncio.sleep(12)

    print("\nСтрока со словом «выдача» - тот набор, который проходит.")


async def main():
    args = sys.argv[1:]
    if args[:1] == ["probe"]:
        await probe()
        return
    print(await avito_monitor.self_test(" ".join(args) or "перфоратор"))


if __name__ == "__main__":
    asyncio.run(main())
