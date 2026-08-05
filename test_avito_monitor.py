# -*- coding: utf-8 -*-
"""Оффлайн-проверка логики монитора на синтетической выдаче Авито."""
import asyncio
import json
import os
import sys
import tempfile
from urllib.parse import quote

os.environ["AVITO_DB"] = tempfile.mktemp(suffix=".db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import avito_monitor as am

fails = []


def check(name, cond, extra=""):
    print(("  OK  " if cond else " FAIL ") + name + (f"  <- {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# --- фикстура: JSON, встроенный в страницу ---
payload = {
    "state": {"catalog": {"items": [
        {"id": 1001, "title": "Перфоратор Bosch GBH 2-26", "priceDetailed": {"value": 3500},
         "urlPath": "/krasnodar/instrumenty/perforator_1001", "geo": {"formattedAddress": "Карасунский"}},
        {"id": 1002, "title": "Перфоратор Makita на запчасти", "priceDetailed": {"value": 900},
         "urlPath": "/krasnodar/instrumenty/perforator_1002", "geo": {"formattedAddress": "ЧМР"}},
        {"id": 1003, "title": "Перфоратор Интерскол", "priceDetailed": {"value": 4800},
         "urlPath": "/krasnodar/instrumenty/perforator_1003", "geo": {}},
        {"id": 1004, "title": "Реклама", "priceDetailed": {"value": 0}, "urlPath": ""},
    ]}},
}
# Старый формат: строка url-кодирована. Встречался до лета 2026.
html_json_old = 'window.__initialData__ = "' + quote(json.dumps(payload, ensure_ascii=False)) + '";'

# Нынешний формат: тот же JSON, но строкой с экранированными кавычками
# внутри. Именно так выглядит window.__preloadedState__ на живой выдаче -
# проверено на сервере 05.08.2026. Внешний json.dumps даёт ровно такое
# экранирование, какое отдаёт Авито.
html_json = ('window.__preloadedState__ = '
             + json.dumps(json.dumps(payload, ensure_ascii=False)) + ';')

check("JSON: старый формат ещё понимается", len(am.parse_from_json(html_json_old)) == 3,
      f"получено {len(am.parse_from_json(html_json_old))}")

items = am.parse_from_json(html_json)
check("JSON: карточки разобраны", len(items) == 3, f"получено {len(items)}")
check("JSON: цена числом", items[0]["price"] == 3500, items[0]["price"])
check("JSON: абсолютный URL", items[0]["url"].startswith("https://www.avito.ru/krasnodar/"), items[0]["url"])
check("JSON: адрес", items[0]["address"] == "Карасунский")
check("JSON: пустой urlPath отброшен", all(i["item_id"] != "1004" for i in items))

# Кавычка в заголовке - ровно то место, где ломался прежний разбор: он брал
# текст «до ближайшей кавычки» и обрывал JSON на первой же экранированной.
tricky = {"state": {"items": [
    {"id": 1005, "title": 'Перфоратор "Зубр" ЗП-1100', "priceDetailed": {"value": 2200},
     "urlPath": "/krasnodar/instrumenty/perforator_1005"},
]}}
html_tricky = ('window.__preloadedState__ = '
               + json.dumps(json.dumps(tricky, ensure_ascii=False)) + ';')
got = am.parse_from_json(html_tricky)
check("JSON: кавычка в заголовке не рвёт разбор",
      len(got) == 1 and got[0]["title"] == 'Перфоратор "Зубр" ЗП-1100', got)

# --- фикстура: разметка ---
html_dom = """
<div data-marker="item" data-item-id="2001">
  <a data-marker="item-title" href="/krasnodar/instrumenty/shurupovert_2001"><h3>Шуруповерт Metabo</h3></a>
  <meta itemprop="price" content="2700">
  <div class="geo-address-x">Прикубанский</div>
</div>
<div data-marker="item" data-item-id="2002">
  <a data-marker="item-title" href="https://www.avito.ru/krasnodar/x_2002"><h3>Шуруповерт DeWalt</h3></a>
  <p data-marker="item-price">4 200 ₽</p>
</div>
"""
dom_items = am.parse_from_dom(html_dom)
check("DOM: карточки разобраны", len(dom_items) == 2, f"получено {len(dom_items)}")
check("DOM: цена из meta", dom_items[0]["price"] == 2700, dom_items[0]["price"])
check("DOM: цена из текста '4 200 ₽'", dom_items[1]["price"] == 4200, dom_items[1]["price"])
check("DOM: готовый http-URL не ломается", dom_items[1]["url"] == "https://www.avito.ru/krasnodar/x_2002")

check("parse_search: fallback на DOM", len(am.parse_search(html_dom)) == 2)
check("parse_search: приоритет JSON", len(am.parse_search(html_json)) == 3)
check("parse_search: мусорная страница -> пусто", am.parse_search("<html>ничего</html>") == [])

# --- мусорные слова ---
check("мусор: 'на запчасти' отсеян", am.is_junk("Перфоратор Makita на запчасти"))
check("мусор: 'куплю' отсеян", am.is_junk("Куплю перфоратор дорого"))
check("мусор: нормальный заголовок проходит", not am.is_junk("Перфоратор Bosch GBH 2-26"))
check("мусор: 'после ремонта' НЕ отсеивается", not am.is_junk("Диван угловой, продаю после ремонта"))
check("мусор: 'требует ремонта' отсеян", am.is_junk("Холодильник Атлант, требует ремонта"))

# --- база и дедупликация ---
am.init_db()
fresh = am.save_items("instrument", "перфоратор", items)
check("база: мусор не сохраняется, осталось два лота", len(fresh) == 2, len(fresh))
check("база: 'на запчасти' не попал в базу", all(f["item_id"] != "1002" for f in fresh))
again = am.save_items("instrument", "перфоратор", items)
check("база: повторный проход не дублирует", len(again) == 0, len(again))

# --- статистика и отбор ---
median, sample = am.median_price("перфоратор")
check("медиана: мало данных -> None", median is None and sample == 2, f"{median}/{sample}")

good, note = am.rate_deal({"title": "Перфоратор Bosch", "price": 3500}, "перфоратор")
check("отбор: без статистики шлём всё в бюджете", good and "копится" in note, note)
good, _ = am.rate_deal({"title": "Перфоратор Bosch", "price": 9000}, "перфоратор")
check("отбор: дороже потолка 5000 - молчим", not good)
good, _ = am.rate_deal({"title": "Перфоратор Bosch", "price": 100}, "перфоратор")
check("отбор: подозрительно дёшево (<300) - молчим", not good)
good, _ = am.rate_deal({"title": "Перфоратор на запчасти", "price": 2000}, "перфоратор")
check("отбор: мусор не проходит", not good)

# набиваем выборку до порога статистики: 12 лотов по 4000
bulk = [{"item_id": f"3{i:03d}", "title": f"Перфоратор ходовой {i}", "price": 4000,
         "url": f"https://www.avito.ru/krasnodar/p_{i}", "address": ""} for i in range(12)]
am.save_items("instrument", "перфоратор", bulk)
median, sample = am.median_price("перфоратор")
check("медиана: считается на выборке", median == 4000 and sample == 14, f"{median}/{sample}")

good, note = am.rate_deal({"title": "Перфоратор Bosch", "price": 2000}, "перфоратор")
check("отбор: 50% от медианы -> горячий лот", good and "🔥" in note, note)
good, note = am.rate_deal({"title": "Перфоратор Bosch", "price": 3000}, "перфоратор")
check("отбор: 75% от медианы -> ниже рынка", good and "ниже рынка" in note, note)
good, note = am.rate_deal({"title": "Перфоратор Bosch", "price": 3900}, "перфоратор")
check("отбор: цена рынка -> не будим", not good, note)

# --- отчёт ---
report = am.build_report(24)
check("отчёт: есть заголовок", "Отчёт по рынку" in report)
check("отчёт: есть категория", "Инструмент и стройка" in report)
check("отчёт: посчитана медиана", "медиана" in report)
check("отчёт: помечено отсутствие продаж", "Продаж за период" in report)

# --- обнаружение блокировки ---
async def blocked_cases():
    class FakeResp:
        def __init__(self, code, text):
            self.status_code, self.text = code, text
        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, resp): self.resp = resp
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return self.resp

    import httpx
    original = httpx.AsyncClient

    for code, body, label in [
        (403, "ok", "HTTP 403"),
        (429, "ok", "HTTP 429"),
        (200, "<html>Доступ ограничен</html>", "капча в теле ответа"),
    ]:
        httpx.AsyncClient = lambda *a, _r=FakeResp(code, body), **kw: FakeClient(_r)
        try:
            await am.fetch_html("https://www.avito.ru/krasnodar")
            check(f"блокировка: {label} распознана", False, "исключения не было")
        except am.AvitoBlocked:
            check(f"блокировка: {label} распознана", True)
        except Exception as exc:
            check(f"блокировка: {label} распознана", False, type(exc).__name__)

    # Заслон Qrator приходит с кодом 429, но лечится не паузами, а сменой IP.
    # Поэтому в тексте ошибки должна быть капча, а не "слишком часто".
    httpx.AsyncClient = lambda *a, _r=FakeResp(
        429, "<html><title>Доступ ограничен: проблема с IP</title></html>"), **kw: FakeClient(_r)
    try:
        await am.fetch_html("https://www.avito.ru/krasnodar")
        check("блокировка: капча с кодом 429 отличается от частых запросов", False, "нет исключения")
    except am.AvitoBlocked as exc:
        check("блокировка: капча с кодом 429 отличается от частых запросов",
              "капча" in str(exc), str(exc))

    httpx.AsyncClient = lambda *a, _r=FakeResp(200, html_json), **kw: FakeClient(_r)
    out = await am.self_test("перфоратор")
    check("self_test: докладывает найденное", "3 карточек" in out and "Bosch" in out, out[:120])
    httpx.AsyncClient = original

asyncio.run(blocked_cases())

# --- URL поиска ---
url = am.build_search_url("сплит система")
check("URL: регион", "/krasnodar?" in url, url)
check("URL: потолок цены", "pmax=5000" in url, url)
check("URL: сортировка по свежести", "s=104" in url, url)
check("URL: пробел закодирован", "%20" in url or "+" in url, url)

# --- настройки ---
am.set_setting("owner_chat", 12345)
check("настройки: чат владельца сохранён", am.get_owner_chat() == 12345)
check("настройки: по умолчанию выключен", not am.is_enabled())
am.set_setting("enabled", "1")
check("настройки: включение работает", am.is_enabled())

print("\n" + ("ВСЁ ЗЕЛЁНОЕ" if not fails else f"ПРОВАЛЕНО: {fails}"))
os.unlink(os.environ["AVITO_DB"])
sys.exit(1 if fails else 0)
