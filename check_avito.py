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
import re
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


# Приметы, по которым видно, что за страница пришла. Ноль по всем сразу -
# значит это не выдача, сколько бы килобайт в ней ни было.
MARKERS = [
    "window.__preloadedState__",
    "window.__initialData__",
    'data-marker="item"',
    'data-marker="item-title"',
    'itemprop="price"',
    "urlPath",
    "priceDetailed",
]


async def dump(query: str):
    """Сохраняет страницу и говорит, что это такое.

    Нужен, когда страница приходит, а карточек в ней не находится: без
    этого невозможно отличить чужую вёрстку от пустой выдачи и от заслона.
    """
    try:
        html = await avito_monitor.fetch_html(avito_monitor.build_search_url(query))
    except avito_monitor.AvitoBlocked as exc:
        print(f"🚫 Выдача не пришла: {exc}")
        print("Защита отпускает волнами - повтори команду через несколько минут.")
        return
    path = "/data/last.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"страница: {len(html)} символов, сохранена в {path}")

    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.I)
    print("заголовок:", title.group(1).strip()[:120] if title else "нет")

    print("\nприметы выдачи:")
    for marker in MARKERS:
        print(f"  {html.count(marker):>6}  {marker}")

    text = re.sub(r"<(script|style).*?</\1>", " ", html, flags=re.DOTALL | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    print("\nвидимый текст, начало:\n ", text[:400])


async def main():
    args = sys.argv[1:]
    if args[:1] == ["probe"]:
        await probe()
        return
    if args[:1] == ["dump"]:
        await dump(" ".join(args[1:]) or "перфоратор")
        return
    print(await avito_monitor.self_test(" ".join(args) or "перфоратор"))


if __name__ == "__main__":
    asyncio.run(main())
