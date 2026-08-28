# -*- coding: utf-8 -*-
"""Оффлайн-проверка логики монитора на синтетической выдаче Авито."""
import asyncio
import datetime
import json
import os
import sys
import tempfile
import time
from urllib.parse import quote

os.environ["AVITO_DB"] = tempfile.mktemp(suffix=".db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import avito_monitor as am

# Проверки оффлайновые, поэтому запросы должны идти через httpx - его легко
# подменить. curl_cffi ходит мимо подмены, прямо в сеть, а браузер вдобавок
# открыл бы окно на каждой проверке.
am.USE_CFFI = False
am.USE_BROWSER = False

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

# Ссылка нужна такая, чтобы по ней можно было зайти и увидеть ту же цену.
# Авито вешает на ссылку в выдаче метку отслеживания на полторы сотни
# знаков - в сообщении это нечитаемо, а для перехода не нужно.
check("ссылка: хвост ?context= отрезан",
      am.item_url("/krasnodar/p_1?context=H4sIAAAAAAAA_wE_AMD_YToyOntzOjEz")
      == "https://www.avito.ru/krasnodar/p_1", am.item_url("/krasnodar/p_1?context=xxx"))
check("ссылка: без хвоста ничего не портится",
      am.item_url("/krasnodar/p_2") == "https://www.avito.ru/krasnodar/p_2")
check("ссылка: готовый абсолютный адрес не задваивается",
      am.item_url("https://www.avito.ru/krasnodar/p_3?context=x")
      == "https://www.avito.ru/krasnodar/p_3")

dom_ctx = am.parse_from_dom(
    '<div data-marker="item" data-item-id="2003">'
    '<a data-marker="item-title" href="/krasnodar/p_2003?context=H4sIAAA"><h3>Диван</h3></a>'
    '<meta itemprop="price" content="3000"></div>')
check("DOM: метка отслеживания не доезжает до сообщения",
      dom_ctx[0]["url"] == "https://www.avito.ru/krasnodar/p_2003", dom_ctx[0]["url"])

check("parse_search: fallback на DOM", len(am.parse_search(html_dom)) == 2)
check("parse_search: приоритет JSON", len(am.parse_search(html_json)) == 3)
check("parse_search: мусорная страница -> пусто", am.parse_search("<html>ничего</html>") == [])

# --- свежесть и подробности карточки ---
# Свежесть для перекупа решает всё: дешёвый лот живёт минуты. Авито пишет
# время словами, точного нигде нет - проверено на живой странице 13.08.2026.
check("возраст: минуты", am.parse_age("2 минуты назад") == 2)
check("возраст: часы", am.parse_age("5 часов назад") == 300)
check("возраст: вчера", am.parse_age("Вчера") == 1440)
check("возраст: дни", am.parse_age("3 дня назад") == 4320)
check("возраст: неделя", am.parse_age("2 недели назад") == 20160)
check("возраст: пусто не роняет", am.parse_age("") is None and am.parse_age("шут его знает") is None)
check("возраст: словами для человека",
      (am.human_age(8), am.human_age(300), am.human_age(1440), am.human_age(4320))
      == ("8 мин назад", "5 ч назад", "вчера", "3 дн назад"),
      (am.human_age(8), am.human_age(300), am.human_age(1440), am.human_age(4320)))

# Район лежит под item-location. Прежний отбор искал имена, которых на
# карточке нет вовсе, и адрес молча приходил пустым - на живой выдаче это
# заметили только сейчас.
html_card = """
<div data-marker="item" data-item-id="7001">
  <a data-marker="item-title" href="/krasnodar/p_7001"><h3>Перфоратор deko</h3></a>
  <meta itemprop="price" content="600">
  <p data-marker="item-date">2 минуты назад</p>
  <div data-marker="item-location">р-н Прикубанский</div>
  <span data-marker="seller-rating/score">5,0</span>
  <span data-marker="seller-info/summary">9 отзывов</span>
</div>
"""
card = am.parse_from_dom(html_card)[0]
check("карточка: район вытащен", card["address"] == "р-н Прикубанский", card["address"])
check("карточка: возраст вытащен", card["age_min"] == 2, card["age_min"])
check("карточка: рейтинг продавца", card["seller_score"] == "5,0", card["seller_score"])
check("карточка: отзывы продавца", card["seller_reviews"] == "9 отзывов", card["seller_reviews"])

# Карточка без этих полей встречается сплошь и рядом - падать нельзя.
bare = am.parse_from_dom(
    '<div data-marker="item" data-item-id="7002">'
    '<a data-marker="item-title" href="/krasnodar/p_7002"><h3>Диван</h3></a>'
    '<meta itemprop="price" content="4000"></div>')[0]
check("карточка: без подробностей не падает",
      bare["age_min"] is None and bare["address"] == "" and bare["seller_score"] == "")

alert = am.build_alert(card, "instrument", "🔥 −82% к сегодняшней выдаче", market=3400)
check("уведомление: цена и рынок рядом", "600 ₽" in alert and "3 400 ₽" in alert, alert)
check("уведомление: показана разница в рублях", "2 800 ₽" in alert, alert)
check("уведомление: свежее помечено особо", "только что" in alert, alert)
check("уведомление: район на месте", "Прикубанский" in alert)
check("уведомление: продавец на месте", "5,0" in alert and "9 отзывов" in alert)
check("уведомление: ссылка на месте", card["url"] in alert)

stale = dict(card, age_min=4320)
check("уведомление: залежавшееся помечено иначе",
      "🐌" in am.build_alert(stale, "instrument", "", market=3400))
check("уведомление: без рынка не выдумывает разницу",
      "разница с рынком" not in am.build_alert(bare, "home", ""))

# --- мусорные слова ---
check("мусор: 'на запчасти' отсеян", am.is_junk("Перфоратор Makita на запчасти"))
check("мусор: 'куплю' отсеян", am.is_junk("Куплю перфоратор дорого"))
check("мусор: нормальный заголовок проходит", not am.is_junk("Перфоратор Bosch GBH 2-26"))
check("мусор: 'после ремонта' НЕ отсеивается", not am.is_junk("Диван угловой, продаю после ремонта"))
check("мусор: 'требует ремонта' отсеян", am.is_junk("Холодильник Атлант, требует ремонта"))

# Услуги мастеров: работа стоит 500-3000 ₽, товаром не является, а медиану
# тянет вниз сильнее всего прочего. Живая выдача 12.08.2026 по «сплит
# система» - все три заголовка настоящие.
check("услуги: 'монтаж и обслуживание' отсеяны",
      am.is_junk("Ремонт монтаж и обслуживание кондиционеров и сплит"))
check("услуги: 'чистка заправка' отсеяны",
      am.is_junk("Ремонт кондиционеров Обслуживание Чистка Заправка"))
check("услуги: 'диагностика' отсеяна", am.is_junk("Диагностика холодильников на дому"))
check("услуги: 'демонтаж' тоже услуга", am.is_junk("Демонтаж кондиционеров недорого"))
# Слово «ремонт» в списке нет и быть не должно: «продаю после ремонта» -
# самая частая формулировка у нормальных объявлений с мебелью и техникой.
check("услуги: голое 'ремонт' по-прежнему не отсеивает",
      not am.is_junk("Диван угловой, продаю после ремонта"))

# --- соответствие объявления запросу ---
# Авито ищет нестрого и подмешивает соседей из чужих категорий. Заголовки
# ниже - с живой выдачи 12.08.2026 по запросу «сплит система».
check("запрос: пластиковые окна к сплит-системам не относятся",
      not am.matches_query("Пластиковые окна двери от производителя", "сплит система"))
check("запрос: чужой товар не проходит",
      not am.matches_query("Перфоратор Bosch GBH 2-26", "сплит система"))
check("запрос: услуга без слов запроса не проходит",
      not am.matches_query("Ремонт кондиционеров Обслуживание Чистка Заправка", "сплит система"))

check("запрос: нормальная сплит-система проходит",
      am.matches_query("Сплит система Ballu 09", "сплит система"))
check("запрос: дефис не мешает - «сплит-система»",
      am.matches_query("Сплит-система Haier 12 новая", "сплит система"))
check("запрос: нормальный перфоратор проходит",
      am.matches_query("Перфоратор Bosch GBH 2-26", "перфоратор"))

# Совпасть должно хотя бы одно слово запроса, а не все сразу: иначе
# выпадет нормальный товар, названный короче, чем мы спрашивали.
check("запрос: хватает одного слова - «Шуруповерт Makita 18V»",
      am.matches_query("Шуруповерт Makita 18V", "шуруповерт аккумуляторный"))
check("запрос: короткое слово 'бу' фильтру не мешает",
      am.matches_query("Кондиционер Ballu настенный", "кондиционер бу"))

# Словоформы: Авито пишет заголовки как придётся, окончания гуляют.
check("запрос: множественное число - «Перфораторы»",
      am.matches_query("Перфораторы Makita новые", "перфоратор"))
check("запрос: родительный падеж - «кондиционера»",
      am.matches_query("Внешний блок кондиционера LG", "внешний блок кондиционера"))
check("запрос: буква «ё» не ломает совпадение",
      am.matches_query("Шуруповёрт Makita", "шуруповерт аккумуляторный"))
check("запрос: короткое слово из трёх букв работает",
      am.matches_query("Болгарка УШМ Makita 125", "болгарка ушм"))

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

# Без истории судим по сегодняшней странице выдачи. Прежде бот в этом
# случае слал всё подряд «чтобы копилась статистика»: при четырёх словах
# терпимо, при сорока - сотни сообщений в день, среди которых настоящие
# находки утонули бы.
page = [{"price": p} for p in [3000, 3100, 3200, 3300, 3400, 3500, 3600, 3700]]
check("страница: медиана считается по выдаче", am.page_median(page) == 3350,
      am.page_median(page))
check("страница: на горстке цен медиана не считается",
      am.page_median([{"price": 3000}, {"price": 4000}]) is None)

good, note = am.rate_deal({"title": "Перфоратор Bosch", "price": 1500},
                          "перфоратор", page_ref=3400)
check("отбор: без истории дешёвое видно по сегодняшней выдаче",
      good and "выдачи" in note, note)
good, _ = am.rate_deal({"title": "Перфоратор Bosch", "price": 3200},
                       "перфоратор", page_ref=3400)
check("отбор: без истории обычная цена не будит", not good)
good, _ = am.rate_deal({"title": "Перфоратор Bosch", "price": 3500}, "перфоратор")
check("отбор: ни истории, ни страницы - молчим, а не шлём всё", not good)
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
# Порог поднят 27.08.2026: прежде на 75% от медианы бот слал «ниже
# медианы», и такие сообщения давали основной поток - 34 штуки за
# четверть часа. Мягкого порога больше нет, будит только 🔥.
good, note = am.rate_deal({"title": "Перфоратор Bosch", "price": 3000}, "перфоратор")
check("отбор: 75% от медианы -> не будим, это не повод ехать", not good, note)
good, note = am.rate_deal({"title": "Перфоратор Bosch", "price": 2400}, "перфоратор")
check("отбор: 60% от медианы -> не будим", not good, note)
good, note = am.rate_deal({"title": "Перфоратор Bosch", "price": 1900}, "перфоратор")
check("отбор: 47% от медианы -> горячий лот", good and "🔥" in note, note)
good, note = am.rate_deal({"title": "Перфоратор Bosch", "price": 3900}, "перфоратор")
check("отбор: цена рынка -> не будим", not good, note)
# Мягкий порог легко вернуть по недосмотру, а вернувшись, он снова зальёт
# владельца потоком. Поэтому проверка не на одну цену, а на всю вилку:
# если бот вообще будит, то только на 🔥.
soft = [price for price in range(300, 5000, 50)
        if (lambda r: r[0] and "🔥" not in r[1])(
            am.rate_deal({"title": "Перфоратор Bosch", "price": price}, "перфоратор"))]
check("отбор: мягкого порога «ниже медианы» больше нет во всей вилке",
      not soft, soft[:5])

# --- живая выдача по «сплит система»: мусор не должен портить медиану ---
# Ровно то, что Авито прислал 12.08.2026: восемь настоящих сплит-систем по
# 4000 ₽ и четыре чужих объявления. Без фильтра медиана из 4000 съезжала
# до 3500 - и настоящие дешёвые находки переставали выглядеть дешёвыми,
# а мусор по 500 ₽ наоборот проходил как «ниже рынка».
split_mix = [
    {"item_id": f"c{i}", "title": f"Сплит система Ballu 09 №{i}", "price": 4000,
     "url": f"https://www.avito.ru/krasnodar/c{i}", "address": ""} for i in range(8)
] + [
    {"item_id": "j1", "title": "Пластиковые окна двери от производителя", "price": 1250,
     "url": "https://www.avito.ru/krasnodar/j1", "address": ""},
    {"item_id": "j2", "title": "Пластиковые окна двери от производителя", "price": 1250,
     "url": "https://www.avito.ru/krasnodar/j2", "address": ""},
    {"item_id": "j3", "title": "Ремонт монтаж и обслуживание кондиционеров и сплит", "price": 3000,
     "url": "https://www.avito.ru/krasnodar/j3", "address": ""},
    {"item_id": "j4", "title": "Ремонт кондиционеров Обслуживание Чистка Заправка", "price": 500,
     "url": "https://www.avito.ru/krasnodar/j4", "address": ""},
]
saved = am.save_items("climate", "сплит система", split_mix)
check("живая выдача: в базу легли только сплит-системы", len(saved) == 8, len(saved))
check("живая выдача: окна отсеяны", all(not s["item_id"].startswith("j") for s in saved),
      [s["item_id"] for s in saved])

median, sample = am.median_price("сплит система")
check("живая выдача: медиана не съехала от мусора", median == 4000 and sample == 8,
      f"{median}/{sample}")
good, _ = am.rate_deal({"title": "Пластиковые окна двери от производителя", "price": 1250},
                       "сплит система")
check("живая выдача: окна не проходят как «ниже рынка»", not good)
good, _ = am.rate_deal({"title": "Ремонт кондиционеров Обслуживание Чистка Заправка",
                        "price": 500}, "сплит система")
check("живая выдача: услуга не проходит как «ниже рынка»", not good)
good, note = am.rate_deal({"title": "Сплит-система Haier 12", "price": 2000}, "сплит система")
check("живая выдача: настоящая дешёвая находка проходит", good and "🔥" in note, note)

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
    am.RETRY_DELAY = 0  # в тестах ждать между попытками незачем

    # Метка Qrator обязана переживать запросы. Получив httpx.Cookies,
    # библиотека делает копию, и всё сложенное туда пропадает - поэтому
    # проверяем именно то, что банка общая, а не одноимённая.
    probe_client = httpx.AsyncClient(cookies=am.COOKIES)
    check("метка: банка общая, а не копия", probe_client.cookies.jar is am.COOKIES)
    await probe_client.aclose()

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

    # Капча с первого захода - обычное дело, а не приговор: вместе с ней
    # приходит метка, и вторая попытка с этой меткой проходит. Раньше бот
    # сдавался сразу и оставался без выдачи на ровном месте.
    sequence = [FakeResp(429, "<html>Доступ ограничен</html>"), FakeResp(200, html_json)]
    httpx.AsyncClient = lambda *a, **kw: FakeClient(sequence.pop(0))
    got = await am.fetch_html("https://www.avito.ru/krasnodar")
    check("блокировка: вторая попытка после капчи проходит",
          "preloadedState" in got, got[:60])

    # Вежливый заслон: вместо выдачи подсовывают главную. Код 200, разметки
    # полмегабайта, объявлений ноль - для программы неотличимо от «ничего не
    # нашлось». Проглотить такое молча значит решить, что рынок пуст.
    home = "<html><title>Авито — Объявления на сайте Авито</title><body>меню</body></html>"
    httpx.AsyncClient = lambda *a, _r=FakeResp(200, home), **kw: FakeClient(_r)
    try:
        await am.fetch_html("https://www.avito.ru/krasnodar")
        check("блокировка: главная вместо выдачи распознана", False, "исключения не было")
    except am.AvitoBlocked as exc:
        check("блокировка: главная вместо выдачи распознана", "главная" in str(exc), str(exc))

    # Ловушка, найденная 12.08.2026 при переводе на браузер: настоящая выдача
    # тоже приходит с обезличенным заголовком, потому что снимок страницы
    # делается раньше, чем javascript перепишет title. Если судить по одному
    # заголовку, живая выдача с полусотней карточек сойдёт за подмену.
    real = ('<html><title>Авито — Объявления на сайте Авито</title>'
            '<body><div data-marker="item" data-item-id="9001">'
            '<a data-marker="item-title" href="/krasnodar/p_9001"><h3>Перфоратор Bosch</h3></a>'
            '<meta itemprop="price" content="2500"></div></body></html>')
    check("подмена: выдача с обезличенным заголовком не считается главной",
          not am.is_home_page(real))
    httpx.AsyncClient = lambda *a, _r=FakeResp(200, real), **kw: FakeClient(_r)
    got = await am.fetch_html("https://www.avito.ru/krasnodar")
    check("подмена: такая выдача доходит до разбора", len(am.parse_search(got)) == 1)

    # А вот честно пустая выдача - законный ответ, на него ругаться нельзя.
    empty = ('<html><title>Перфораторы купить в Краснодаре</title>'
             '<body>Ничего не найдено</body></html>')
    httpx.AsyncClient = lambda *a, _r=FakeResp(200, empty), **kw: FakeClient(_r)
    check("блокировка: пустая выдача заслоном не считается",
          await am.fetch_html("https://www.avito.ru/krasnodar") == empty)

    httpx.AsyncClient = lambda *a, _r=FakeResp(200, html_json), **kw: FakeClient(_r)
    out = await am.self_test("перфоратор")
    check("self_test: докладывает найденное", "3 карточек" in out and "Bosch" in out, out[:120])
    httpx.AsyncClient = original

asyncio.run(blocked_cases())

# --- техника Apple: своя вилка цен и два алфавита ---
# Общий потолок 5000 ₽ писался под инструмент. Если он подтянется к айфонам,
# выдача станет пустой, и выглядеть это будет как «Авито опять закрылся».
low, high = am.price_band("iphone")
check("Apple: у айфонов своя вилка, а не общая", (low, high) == (40000, 95000), (low, high))
check("Apple: у часов своя вилка", am.price_band("applewatch") == (6000, 45000),
      am.price_band("applewatch"))
check("вилка: у категории без своей берётся общая",
      am.price_band("instrument") == (am.MIN_PRICE, am.MAX_PRICE), am.price_band("instrument"))
check("вилка: неизвестная категория не роняет, берётся общая",
      am.price_band("чего-то нет") == (am.MIN_PRICE, am.MAX_PRICE))
check("вилка: категория определяется по слову запроса",
      am.category_of("iphone 17 pro") == "iphone" and am.category_of("перфоратор") == "instrument")

url_apple = am.build_search_url("iphone 17 pro")
check("Apple: вилка уходит в адрес поиска",
      "pmin=40000" in url_apple and "pmax=95000" in url_apple, url_apple)
check("URL: у инструмента вилка прежняя",
      "pmax=5000" in am.build_search_url("перфоратор"), am.build_search_url("перфоратор"))

# Одна и та же вещь пишется двумя алфавитами. Без приведения к общему виду
# фильтр «про то ли объявление» выбросил бы половину живой выдачи.
check("Apple: «Айфон 17 Про» совпадает с запросом iphone 17 pro",
      am.matches_query("Айфон 17 Про Макс 256 ГБ", "iphone 17 pro"))
check("Apple: латинское написание тоже совпадает",
      am.matches_query("iPhone 17 Pro 256GB", "iphone 17 pro"))
check("Apple: «Эппл вотч» совпадает с запросом apple watch",
      am.matches_query("Умные часы Эппл Вотч 9", "apple watch"))
check("Apple: Apple Watch латиницей совпадает",
      am.matches_query("Apple Watch Series 10 46mm", "apple watch"))

# А вот чего быть не должно. «Часы» намеренно не считаются словом watch:
# иначе за Apple Watch сошли бы любые часы, ведь для совпадения хватает
# одного слова.
check("Apple: чужие часы за Apple Watch не выдаются",
      not am.matches_query("Часы Casio G-Shock", "apple watch"))
check("Apple: чужой телефон не выдаётся за айфон",
      not am.matches_query("Samsung Galaxy S24 Ultra", "iphone 17 pro"))
# «Про» осталось предлогом, а не словом Pro - иначе совпадало бы что угодно.
check("Apple: «продам про запас» не считается айфоном",
      not am.matches_query("Продам про запас ящик гвоздей", "iphone 17 pro"))

check("Apple: копии отсеиваются", am.is_junk("iPhone 17 Pro копия 1в1"))
check("Apple: реплики отсеиваются", am.is_junk("Apple Watch реплика премиум"))
check("Apple: магазинные «в рассрочку» отсеиваются",
      am.is_junk("iPhone 17 Pro новый, рассрочка 0%"))
check("Apple: нормальное объявление проходит",
      not am.is_junk("iPhone 17 Pro 256 ГБ, идеальное состояние"))

# Отбор находки должен считать по вилке своей категории. Айфон за 70 тысяч
# в общий потолок 5000 ₽ не влезал бы и молча пропадал.
good, _ = am.rate_deal({"title": "iPhone 17 Pro 256", "price": 45000},
                       "iphone 17 pro", page_ref=92000)
check("Apple: дешёвый айфон проходит по своей вилке и будит", good)
good, _ = am.rate_deal({"title": "iPhone 17 Pro 256", "price": 20000}, "iphone 17 pro")
check("Apple: айфон за 20 000 отсеян - столько рабочий не стоит", not good)
good, _ = am.rate_deal({"title": "Перфоратор Bosch", "price": 70000}, "перфоратор")
check("вилка: перфоратор за 70 000 по-прежнему отсеивается", not good)

# --- выбор категории при проверке ---
# Проверка была намертво зашита на «перфоратор»: увидеть по ней айфоны было
# нельзя в принципе, сколько ни нажимай. Теперь кнопка спрашивает, что
# смотреть, - и в списке обязаны быть все категории, включая новые.
kb = am.test_keyboard()
labels = [b.text for row in kb.inline_keyboard for b in row]
codes = [b.callback_data for row in kb.inline_keyboard for b in row]
check("выбор: кнопок столько же, сколько категорий",
      len(labels) == len(am.CATEGORIES), labels)
check("выбор: айфоны есть среди кнопок",
      any("Айфоны" in text for text in labels), labels)
check("выбор: часы Apple есть среди кнопок",
      any("Часы Apple" in text for text in labels), labels)
check("выбор: у каждой кнопки свой опознаватель",
      len(set(codes)) == len(codes) and all(c.startswith(am.TEST_PREFIX) for c in codes),
      codes)


async def test_choice_runs():
    """Нажатие кнопки должно привести к проверке слова именно этой категории."""
    asked = []

    async def fake_self_test(query, category=None):
        asked.append((query, category))
        return "готово"

    class FakeMessage:
        def __init__(self): self.replies = []
        async def reply_text(self, text, **kw): self.replies.append(text)

    class FakeCallback:
        def __init__(self, data):
            self.data, self.message, self.answered = data, FakeMessage(), False
        async def answer(self, *a, **kw): self.answered = True

    class FakeUpdate:
        def __init__(self, data): self.callback_query = FakeCallback(data)

    saved, am.self_test = am.self_test, fake_self_test
    try:
        upd = FakeUpdate(am.TEST_PREFIX + "iphone")
        await am.on_test_choice(upd, None)
        check("выбор: нажатие подтверждено, иначе на кнопке висят часики",
              upd.callback_query.answered)
        # Категория передаётся вместе со словом, а не угадывается по нему:
        # «apple watch» есть и у Авито, и у Wildberries.
        check("выбор: проверяется слово выбранной категории",
              asked == [("iphone 17 pro", "iphone")], asked)
        joined = " ".join(upd.callback_query.message.replies)
        check("выбор: назван коридор цен этой категории",
              "40 000" in joined and "95 000" in joined, joined[:90])

        # Чужой или устаревший опознаватель не должен ронять бота.
        bad = FakeUpdate(am.TEST_PREFIX + "выдумка")
        await am.on_test_choice(bad, None)
        check("выбор: неизвестная категория не роняет бота",
              "Не знаю" in " ".join(bad.callback_query.message.replies))
    finally:
        am.self_test = saved


asyncio.run(test_choice_runs())

# --- вторая площадка: Wildberries ---
# Проверено 13.08.2026 живым браузером: WB отдаёт 40 карточек, Ozon - 403
# «нет соединения», Яндекс Маркет - 403 «похоже, вы используете VPN».
# Поэтому площадки две, а не четыре.
import wb_source as wb

wb_html = """
<article>
  <a href="https://www.wildberries.ru/catalog/1212147831/detail.aspx?x=1"></a>
  <div class="product-card__price price">
    <ins>41 468 ₽</ins><del>106 058 ₽</del>
  </div>
  <div class="wrap">
    <span class="brandContainer--b1Fxa">Apple</span><span>/</span>
    <span>Смартфон iPhone 15 128 ГБ, черный Восстановленный</span>
  </div>
  <span class="ratingContent--ZYqdV">5 · 2 оценки</span>
</article>
"""
wb_items = wb.parse(wb_html)
check("WB: карточка разобрана", len(wb_items) == 1, len(wb_items))
if wb_items:
    w = wb_items[0]
    check("WB: цена", w["price"] == 41468, w["price"])
    check("WB: марка и название вместе",
          w["title"] == "Apple Смартфон iPhone 15 128 ГБ, черный Восстановленный", w["title"])
    check("WB: ссылка без хвоста",
          w["url"] == "https://www.wildberries.ru/catalog/1212147831/detail.aspx", w["url"])
    # Номера товаров WB и объявлений Авито в одной базе; без приставки они
    # рано или поздно совпали бы, и одно затёрло бы другое.
    check("WB: номер с приставкой, чтобы не столкнуться с Авито",
          w["item_id"] == "wb1212147831", w["item_id"])
    check("WB: рейтинг снят", "5" in w["seller_score"], w["seller_score"])
    # Старая цена нарисованная: iPhone 15 никогда не стоил 106 058 ₽.
    # Забираем для показа, но судить по ней нельзя.
    check("WB: старая цена сохранена отдельно", w["old_price"] == 106058, w["old_price"])

check("WB: пустая страница не роняет разбор", wb.parse("<html>ничего</html>") == [])
check("WB: заслон распознан",
      wb.is_blocked("<html>Похоже, нет соединения</html>"))
check("WB: выдача заслоном не считается", not wb.is_blocked(wb_html))
check("WB: адрес поиска со словом и сортировкой по новизне",
      "search=iphone" in wb.build_search_url("iphone") and
      "sort=newly" in wb.build_search_url("iphone"), wb.build_search_url("iphone"))

check("площадка: у категории WB источник wb", am.source_of("wb_apple") == "wb")
check("площадка: у обычной категории источник авито",
      am.source_of("instrument") == "avito" and am.source_of(None) == "avito")

# Цены копятся порознь. «iphone» с рук и «iphone» из магазина - разные
# рынки; общая медиана врала бы обоим сразу.
check("площадка: имя для статистики у WB своё",
      am.stats_key("wb", "iphone") == "wb:iphone")
check("площадка: у Авито имя прежнее, старая история не потеряется",
      am.stats_key("avito", "перфоратор") == "перфоратор")

check("площадка: адрес поиска WB строится через wb_source",
      "wildberries.ru" in am.build_search_url("iphone", "wb_apple"),
      am.build_search_url("iphone", "wb_apple"))
check("площадка: адрес Авито не изменился",
      "avito.ru" in am.build_search_url("перфоратор"), am.build_search_url("перфоратор"))
check("площадка: разбор выбирается по источнику",
      len(am.parse_search(wb_html, "wb")) == 1 and len(am.parse_search(html_dom)) == 2)


async def wb_self_test_goes_to_wb():
    """Проверка категории WB обязана идти на WB, а не на Авито.

    Сперва так и было сломано: адрес строился вэбэшный, а дальше всё
    по-авитовски - прогрев через главную Авито, ожидание авитовской приметы,
    авитовский разбор. Выдача приходила живая, а бот докладывал «ноль
    карточек, Авито сменил вёрстку».
    """
    seen = {}

    async def fake_fetch(url, source="avito"):
        seen["url"], seen["source"] = url, source
        return wb_html

    saved, am.fetch_html = am.fetch_html, fake_fetch
    try:
        out = await am.self_test("iphone", "wb_apple")
        check("WB: проверка идёт на адрес Wildberries",
              "wildberries.ru" in seen.get("url", ""), seen.get("url"))
        check("WB: добыча знает, что площадка не Авито",
              seen.get("source") == "wb", seen.get("source"))
        check("WB: в ответе названа верная площадка",
              "Wildberries" in out and "1" in out, out[:80])
        check("WB: разобрана карточка, а не «ноль»", "Ноль карточек" not in out, out[:80])

        # А проверка авитовской категории по-прежнему идёт на Авито.
        await am.self_test("перфоратор", "instrument")
        check("площадка: проверка Авито не съехала на WB",
              "avito.ru" in seen["url"] and seen["source"] == "avito", seen)
    finally:
        am.fetch_html = saved


asyncio.run(wb_self_test_goes_to_wb())

# --- URL поиска ---
url = am.build_search_url("сплит система")
check("URL: регион", "/krasnodar?" in url, url)
check("URL: потолок цены", "pmax=5000" in url, url)
check("URL: сортировка по свежести", "s=104" in url, url)
check("URL: пробел закодирован", "%20" in url or "+" in url, url)

# --- как показываем прокси ---
# Пароль от прокси на экране - это тот же пароль на скриншотах, а токен бота
# уже дважды так утекал. Показываем только адрес с портом.
saved_proxy = am.AVITO_PROXY
check("прокси: без настройки честно говорим, что идём напрямую",
      "напрямую" in am.proxy_label(), am.proxy_label())
am.AVITO_PROXY = "http://user12345:secret@45.67.89.10:8000"
check("прокси: логин и пароль не показываются",
      am.proxy_label() == "http://45.67.89.10:8000", am.proxy_label())
am.AVITO_PROXY = "socks5://45.67.89.10:18360"
check("прокси: без логина строка не задваивается",
      am.proxy_label() == "socks5://45.67.89.10:18360", am.proxy_label())
am.AVITO_PROXY = saved_proxy

# --- добыча страницы браузером ---
# Скрытый браузер Авито распознаёт: 12.08.2026 три варианта подряд (скрытый
# Chromium, он же в новом режиме, настоящий Chrome скрытым) получили HTTP 429
# и «Доступ ограничен». Видимый на том же интернете и том же адресе - 50
# карточек. Умолчание «видимый» здесь не мелочь: если оно тихо съедет на
# скрытый, монитор снова начнёт получать капчу, а выглядеть это будет как
# «Авито опять забанил» - именно на таком мы уже потеряли несколько дней.
import avito_browser as ab

check("браузер: по умолчанию видимый, а не скрытый", not ab.HEADLESS)
check("браузер: по умолчанию берётся установленный Chrome", ab.CHANNEL == "chrome", ab.CHANNEL)
# На сервере вместо «канала» указывается прямой путь: свой Chromium
# Playwright качает со склада, до которого оттуда полтора часа и обрыв,
# а в Debian тот же браузер ставится за минуту.
check("браузер: дома путь к файлу не задан, работает выбор по каналу",
      ab.EXECUTABLE == "", ab.EXECUTABLE)
check("браузер: умеет доложить о себе", "браузер" in ab.label(), ab.label())

# Профиль обязан переживать перезапуски: на чистом каждый запуск начинается
# с капчи. Замер 12.08.2026: прогретый профиль - 49 карточек, чистый - 429.
check("браузер: профиль лежит в постоянной папке, а не во временной",
      ab.PROFILE_DIR and "Temp" not in ab.PROFILE_DIR, ab.PROFILE_DIR)

# Заслон определяется не только по коду ответа: настоящая выдача приходила
# и с кодом 429, и с чужим заголовком. Карточки - решающий признак.
check("браузер: страница с карточками заслоном не считается",
      not ab._looks_blocked(429, '<html><div data-marker="item"></div></html>'))
check("браузер: капча без карточек распознана",
      ab._looks_blocked(429, "<html><title>Доступ ограничен</title></html>"))
check("браузер: пустая выдача заслоном не считается",
      not ab._looks_blocked(200, "<html>Ничего не найдено</html>"))

saved_browser_proxy = ab.PROXY
ab.PROXY = ""
check("браузер: без прокси настройка пустая", ab._proxy_settings() is None)
ab.PROXY = "http://user12345:secret@45.67.89.10:8000"
settings = ab._proxy_settings()
check("браузер: адрес прокси отделён от логина",
      settings["server"] == "http://45.67.89.10:8000", settings)
check("браузер: логин и пароль разложены по полям",
      settings["username"] == "user12345" and settings["password"] == "secret", settings)
ab.PROXY = "socks5://45.67.89.10:18360"
check("браузер: прокси без логина не ломается",
      ab._proxy_settings() == {"server": "socks5://45.67.89.10:18360"}, ab._proxy_settings())
ab.PROXY = saved_browser_proxy


async def browser_status_codes():
    """У браузера код ответа не улика: живое объявление приходило с 439."""
    calls = []

    async def fake_fetch(url):
        calls.append(url)
        return 439, ('<html><title>iPhone 17 Pro купить в Краснодаре</title>'
                     '<body><span itemprop="price" content="95000"></span></body></html>')

    original_fetch, ab.fetch = ab.fetch, fake_fetch
    am.USE_BROWSER = True
    try:
        page = await am.fetch_html("https://www.avito.ru/krasnodar/telefony/iphone_1")
        check("код ответа: страница с кодом 439 через браузер принимается",
              "iPhone 17 Pro" in page, page[:60])
    except am.AvitoBlocked as exc:
        check("код ответа: страница с кодом 439 через браузер принимается", False, str(exc))
    finally:
        ab.fetch = original_fetch
        am.USE_BROWSER = False

    # А по http код по-прежнему решающий: там врать нечему.
    import httpx

    class R:
        def __init__(self): self.status_code, self.text = 503, "<html>беда</html>"

    class C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return R()

    saved = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: C()
    try:
        await am.fetch_html("https://www.avito.ru/krasnodar")
        check("код ответа: по http код 503 по-прежнему отказ", False, "исключения не было")
    except am.AvitoBlocked:
        check("код ответа: по http код 503 по-прежнему отказ", True)
    finally:
        httpx.AsyncClient = saved


asyncio.run(browser_status_codes())


async def browser_route():
    """Через браузер ли идём, когда так велено, и мимо ли него - когда нет."""
    calls = []

    async def fake_fetch(url):
        calls.append(url)
        return 200, html_json

    original_fetch, ab.fetch = ab.fetch, fake_fetch
    am.USE_BROWSER = True
    try:
        status, body = await am._fetch_once("https://www.avito.ru/krasnodar")
        check("браузер: запрос уходит в браузер, а не в httpx",
              calls == ["https://www.avito.ru/krasnodar"] and status == 200, calls)
        check("браузер: разбор выдачи не меняется", len(am.parse_search(body)) == 3)
        check("браузер: диагностика называет способ", "браузер" in am.fetch_label(),
              am.fetch_label())
    finally:
        ab.fetch = original_fetch
        am.USE_BROWSER = False

    check("браузер: с AVITO_FETCH=http способ прежний",
          am.fetch_label() in ("httpx", "Chrome через curl_cffi"), am.fetch_label())


asyncio.run(browser_route())

# --- канал с находками ---
# Затея простая на словах и коварная на деле: канал не должен обгонять
# владельца, иначе подписчики забирают лоты у того, кто их собрал.
am.CHANNEL_RATIO = 0.7
check("канал: без настройки канала нет", am.get_channel_chat() is None)

# Порог канала мягче владельцева. Одна и та же карточка должна проходить
# в канал и не проходить в личку - иначе вся затея с двумя мерками пустая.
mid = {"title": "Перфоратор Bosch", "price": 2800, "item_id": "ch1",
       "url": "https://www.avito.ru/krasnodar/ch1", "address": "Центр"}
own, _ = am.rate_deal(mid, "перфоратор")
ch, ch_note = am.rate_deal(mid, "перфоратор", ratio=am.CHANNEL_RATIO)
check("канал: 70% от медианы - владельцу молчим", not own)
check("канал: 70% от медианы - в канал идёт", ch, ch_note)
check("канал: огонёк на такое не ставится", "🔥" not in ch_note, ch_note)

hot = dict(mid, price=1800, item_id="ch2")
own, own_note = am.rate_deal(hot, "перфоратор")
_, ch_note = am.rate_deal(hot, "перфоратор", ratio=am.CHANNEL_RATIO)
check("канал: вдвое дешевле - и владельцу, и в канал", own)
check("канал: огонёк означает одно и то же в обоих местах",
      "🔥" in own_note and "🔥" in ch_note, f"{own_note} / {ch_note}")

plain = dict(mid, price=3900, item_id="ch3")
ch, _ = am.rate_deal(plain, "перфоратор", ratio=am.CHANNEL_RATIO)
check("канал: цена рынка не идёт даже в канал", not ch)

# Очередь. Она в базе, а не в памяти: дома бот перезапускается после
# каждого обрыва связи, и полчаса ожидания это переживают запросто.
am.save_items("instrument", "перфоратор", [mid, hot, plain])
am.queue_for_channel(mid, "instrument", "дешевле медианы", 4000)
check("очередь: до срока карточка не выдаётся", am.due_for_channel() == [])

am.queue_for_channel(hot, "instrument", "🔥 дешевле", 4000, delay=-1)
due = am.due_for_channel()
check("очередь: отстоявшая срок карточка выдаётся", len(due) == 1, len(due))
check("очередь: выдаётся вместе с данными объявления",
      due and due[0]["title"] == "Перфоратор Bosch" and due[0]["price"] == 1800, due)
check("очередь: выдаётся с заготовленной пометкой",
      due and due[0]["note"] == "🔥 дешевле", due)

am.queue_for_channel(hot, "instrument", "другая пометка", 9999, delay=-1)
again = am.due_for_channel()
check("очередь: та же карточка второй раз не встаёт",
      len(again) == 1 and again[0]["note"] == "🔥 дешевле", again)

am.mark_posted(hot["item_id"])
check("очередь: выложенная карточка больше не выдаётся", am.due_for_channel() == [])

# После суток простоя в очереди накопится десяток находок. Вываливать их
# залпом нельзя: Telegram ответит отказом по частоте, а читатель получит
# стену сообщений.
for i in range(9):
    lot = dict(mid, item_id=f"heap{i}", url=f"https://www.avito.ru/krasnodar/heap{i}")
    am.save_items("instrument", "перфоратор", [lot])
    am.queue_for_channel(lot, "instrument", "дешевле медианы", 4000, delay=-1)
check("очередь: за раз берётся горсть, а не весь завал",
      len(am.due_for_channel()) == 5, len(am.due_for_channel()))

# Выкладка. Бота подделываем: сеть здесь не нужна, как и везде.
class FakeBot:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail:
            raise RuntimeError("нет прав на публикацию")
        self.sent.append((chat_id, text))

am.CHANNEL_DELAY = 0
bot = FakeBot()
check("выкладка: без настроенного канала ничего не уходит",
      asyncio.run(am.post_due_to_channel(bot)) == 0 and not bot.sent)

am.set_setting("channel_chat", "@krd_nahodki")
check("канал: публичное имя возвращается строкой", am.get_channel_chat() == "@krd_nahodki")
am.set_setting("channel_chat", "-1001234567890")
check("канал: номер закрытого канала возвращается числом",
      am.get_channel_chat() == -1001234567890)

bot = FakeBot()
posted = asyncio.run(am.post_due_to_channel(bot))
check("выкладка: горсть ушла в канал", posted == 5 and len(bot.sent) == 5, posted)
check("выкладка: ушла именно в канал, а не владельцу",
      all(c == -1001234567890 for c, _ in bot.sent), [c for c, _ in bot.sent])
check("выкладка: в сообщении есть ссылка на объявление",
      all("avito.ru" in t for _, t in bot.sent))
check("выкладка: выложенное во второй раз не уходит",
      asyncio.run(am.post_due_to_channel(FakeBot())) == 4)

# Нет прав на публикацию - самая частая беда с каналами. Находка при этом
# не должна пропасть: полежит и уйдёт следующей попыткой.
before = len(am.due_for_channel())
check("выкладка: отказ канала не съедает очередь",
      asyncio.run(am.post_due_to_channel(FakeBot(fail=True))) == 0
      and len(am.due_for_channel()) == before, before)

am.set_setting("channel_chat", "")

# --- сквозной прогон круга монитора ---
# Всё вокруг цикла проверено, а сам цикл - главный кусок бота - до сих пор
# не запускался ни разу. Именно там сходятся вместе запрос, разбор, база,
# две мерки, личка и канал; ошибка в проводке между ними не видна ни в
# одной проверке по отдельности.
def make_page(lots):
    """Страница выдачи в нынешнем виде Авито."""
    body = {"state": {"catalog": {"items": [
        {"id": lot["id"], "title": lot["title"],
         "priceDetailed": {"value": lot["price"]},
         "urlPath": f"/krasnodar/instrumenty/lot_{lot['id']}",
         "geo": {"formattedAddress": "Фестивальный"}} for lot in lots]}}}
    return "window.__preloadedState__ = " + json.dumps(json.dumps(body, ensure_ascii=False)) + ";"


class LoopBot:
    def __init__(self):
        self.messages = []          # (chat_id, text)

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))


async def run_one_cycle(pages, chat_owner=777, channel=None, ratio=None):
    """Гоняет ровно один круг монитора и возвращает бота с тем, что он отправил.

    Цикл вечный, поэтому останавливаем его отменой на паузе между кругами:
    так проверяется именно то, что цикл делает за круг, а не выдуманная
    его перепевка.
    """
    am.set_setting("enabled", "1")
    am.set_setting("owner_chat", str(chat_owner))
    am.set_setting("channel_chat", channel or "")
    am.CHANNEL_DELAY = -1                       # в канал сразу, без ожидания
    am.CHANNEL_SEND_PAUSE = 0                   # и без секундной паузы между постами
    if ratio is not None:
        am.CHANNEL_RATIO = ratio

    bot = LoopBot()
    served = []

    async def fake_fetch(url, source="avito"):
        served.append(url)
        return pages[(len(served) - 1) % len(pages)]

    real_fetch, real_pause, real_min, real_max = (
        am.fetch_html, am.CYCLE_PAUSE, am.REQ_DELAY_MIN, am.REQ_DELAY_MAX)
    am.fetch_html = fake_fetch
    am.CYCLE_PAUSE, am.REQ_DELAY_MIN, am.REQ_DELAY_MAX = 3600, 0, 0
    try:
        task = asyncio.create_task(am.monitor_loop(bot))
        # Даём кругу отработать и уснуть на CYCLE_PAUSE, затем снимаем.
        for _ in range(400):
            await asyncio.sleep(0)
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        am.fetch_html = real_fetch
        am.CYCLE_PAUSE, am.REQ_DELAY_MIN, am.REQ_DELAY_MAX = real_pause, real_min, real_max
        am.set_setting("enabled", "0")
        am.set_setting("channel_chat", "")
        am.CHANNEL_DELAY = 1800
        am.CHANNEL_SEND_PAUSE = 1
    return bot, served


# Рынок: девять перфораторов около 4000 - чтобы медиана была настоящей.
am.QUERIES_PER_CYCLE = 1
am._query_cursor = 0
am.save_items("instrument", "перфоратор", [
    {"item_id": f"loop_bg{i}", "title": f"Перфоратор рабочий {i}", "price": 4000,
     "url": f"https://www.avito.ru/krasnodar/bg{i}", "address": ""} for i in range(12)])

first = am.all_queries()[0]                     # с него цикл и начнёт
cat_key, query = first
bulk = [{"item_id": f"m{i}", "title": f"{query} ходовой {i}", "price": 4000,
         "url": f"https://www.avito.ru/krasnodar/m{i}", "address": ""} for i in range(12)]
am.save_items(cat_key, am.stats_key(am.source_of(cat_key), query), bulk)

hot = {"id": 90001, "title": f"{query} Bosch, срочно", "price": 1600}    # 40% рынка
mid = {"id": 90002, "title": f"{query} Makita", "price": 2800}           # 70% рынка
plain = {"id": 90003, "title": f"{query} обычный", "price": 3900}        # рынок
page = make_page([hot, mid, plain])

bot, served = asyncio.run(run_one_cycle([page]))
owner_texts = [t for c, t in bot.messages if c == 777]
check("круг: запрос к площадке ушёл", len(served) == 1, served)
check("круг: владельцу ушла ровно одна находка", len(owner_texts) == 1, owner_texts)
check("круг: владельцу ушла именно горячая",
      owner_texts and "1 600" in owner_texts[0] and "🔥" in owner_texts[0],
      owner_texts[0][:120] if owner_texts else None)
def in_db(item_id):
    with am._connect() as c:
        return c.execute("SELECT 1 FROM items WHERE item_id = ?",
                         (str(item_id),)).fetchone() is not None


check("круг: в базу легли все карточки, а не только разосланные",
      in_db(90001) and in_db(90002) and in_db(90003))

# Тот же круг, но с каналом. Карточки нужны новые: те, что уже в базе,
# новыми не считаются и в рассылку не идут - иначе канал получал бы один и
# тот же лот каждый круг, пока объявление висит.
am._query_cursor = 0
page2 = make_page([
    {"id": 90011, "title": f"{query} Bosch, срочно", "price": 1600},
    {"id": 90012, "title": f"{query} Makita", "price": 2800},
    {"id": 90013, "title": f"{query} обычный", "price": 3900},
])
bot, served = asyncio.run(run_one_cycle([page2], channel="@krd_nahodki", ratio=0.7))
owner_texts = [t for c, t in bot.messages if c == 777]
channel_texts = [t for c, t in bot.messages if c == "@krd_nahodki"]
check("круг с каналом: владельцу ушла только горячая",
      len(owner_texts) == 1 and "1 600" in owner_texts[0], owner_texts)
check("круг с каналом: в канал ушли обе находки", len(channel_texts) == 2, channel_texts)
check("круг с каналом: мягкая находка попала только в канал",
      any("2 800" in t for t in channel_texts)
      and not any("2 800" in t for t in owner_texts), channel_texts)
check("круг с каналом: рыночная цена не ушла никуда",
      not any("3 900" in t for _, t in bot.messages), bot.messages)

# Блокировка Авито посреди круга: цикл не должен ни падать, ни молчать.
am._query_cursor = 0
am.set_setting("enabled", "1")
am.set_setting("owner_chat", "777")
blocked_bot = LoopBot()


async def blocking_fetch(url, source="avito"):
    raise am.AvitoBlocked("капча Qrator")


real_fetch, real_pause = am.fetch_html, am.CYCLE_PAUSE
am.fetch_html, am.CYCLE_PAUSE = blocking_fetch, 3600


async def run_blocked():
    task = asyncio.create_task(am.monitor_loop(blocked_bot))
    for _ in range(200):
        await asyncio.sleep(0)
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


asyncio.run(run_blocked())
am.fetch_html, am.CYCLE_PAUSE = real_fetch, real_pause
am.set_setting("enabled", "0")
check("круг: про блокировку владельцу сказано, а не проглочено",
      any("ограничил доступ" in t for _, t in blocked_bot.messages), blocked_bot.messages)

am.QUERIES_PER_CYCLE = 4

# --- деньги с пробелами ---
# Замена «,» на пробел по всей готовой строке выгрызала запятую из середины
# названия: у «Перфоратор Bosch, отличное состояние» она пропадала.
check("деньги: разряды разделены пробелом", am.money(1234567) == "1 234 567", am.money(1234567))
alert = am.build_alert(
    {"title": "Перфоратор Bosch, отличное состояние, торг", "price": 3200,
     "url": "https://www.avito.ru/krasnodar/z1", "address": "Центр, Фестивальный"},
    "instrument", "🔥 дешевле", 7500)
check("деньги: запятая в названии не съедена",
      "Bosch, отличное состояние, торг" in alert, alert.split("\n")[2])
check("деньги: запятая в районе не съедена", "Центр, Фестивальный" in alert)
check("деньги: цена всё равно с пробелом", "3 200" in alert and "7 500" in alert)

# --- недельные посты в канал ---
# Канал, где пусто шесть дней и густо в седьмой, отписывают. Эти два поста
# дают ленте жизнь даже в неделю без единой находки.
# Постить нечего - значит молчим. Проверяется нулевым окном: за «последние
# ноль дней» в базе не может быть ничего, сколько бы в ней ни лежало.
check("недельный пост: когда нечего сказать - сводки нет",
      am.build_channel_digest(days=0) == "", am.build_channel_digest(days=0)[:60])
check("недельный пост: когда нечего сказать - разбора нет",
      am.build_sold_proof(days=0) == "", am.build_sold_proof(days=0)[:60])

week = [{"item_id": f"w{i}", "title": f"Перфоратор Bosch, как новый {i}",
         "price": 3000 + i * 200, "url": f"https://www.avito.ru/krasnodar/w{i}",
         "address": ""} for i in range(9)]
am.save_items("instrument", "перфоратор", week)
for i, (mkt, ago) in enumerate([(7500, 30), (6000, 9)]):
    am.queue_for_channel(week[i], "instrument", "🔥 дешевле", mkt, delay=-1)
    with am._connect() as c:
        c.execute("UPDATE channel_queue SET posted_at = ? WHERE item_id = ?",
                  (time.time() - ago * 3600, week[i]["item_id"]))
with am._connect() as c:
    c.execute("UPDATE items SET sold_at = ? WHERE item_id = 'w0'", (time.time() - 6 * 3600,))

digest = am.build_channel_digest()
check("сводка: считает просмотренное", "Просмотрено объявлений" in digest, digest[:60])
check("сводка: показывает медиану по оживлённой категории",
      "Инструмент и стройка" in digest and "медиана" in digest)
check("сводка: запятые в названии находки целы",
      "Bosch, как новый" in digest, digest[-160:])
check("сводка: находкой недели названа самая выгодная",
      "−60%" in digest, digest[-120:])

proof = am.build_sold_proof()
check("разбор: показывает снятое с публикации", "w" not in proof and "3 000" in proof, proof[:90])
check("разбор: показывает, за сколько часов ушло", "ушло за" in proof, proof[:200])
check("разбор: не обещает продажу, которой не видел",
      "снято с Авито" in proof and "продано" not in proof.lower(), proof[:120])

# Расписание. Отметка о выкладке - в базе: дома бот перезапускается по
# нескольку раз в день, и памятью он бы её не удержал.
class Poster:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(text)


am.set_setting("channel_chat", "@krd_nahodki")
am.CHANNEL_POST_HOUR = 0                      # чтобы проверка не зависела от часа
today = datetime.datetime.now()
am.CHANNEL_DIGEST_DAY = str(today.weekday())
am.CHANNEL_PROOF_DAY = str((today.weekday() + 1) % 7)
am.set_setting("channel_digest_date", "")

bot = Poster()
check("расписание: в свой день сводка уходит",
      asyncio.run(am.post_weekly(bot)) == "digest" and len(bot.sent) == 1, bot.sent)
check("расписание: второй раз за день не повторяется",
      asyncio.run(am.post_weekly(bot)) is None and len(bot.sent) == 1, len(bot.sent))

am.CHANNEL_DIGEST_DAY = str((today.weekday() + 3) % 7)
am.CHANNEL_PROOF_DAY = str((today.weekday() + 4) % 7)
bot = Poster()
check("расписание: не в свой день молчит",
      asyncio.run(am.post_weekly(bot)) is None and not bot.sent)

am.CHANNEL_POST_HOUR = 23
am.CHANNEL_DIGEST_DAY = str(today.weekday())
am.set_setting("channel_digest_date", "")
check("расписание: раньше назначенного часа молчит",
      asyncio.run(am.post_weekly(Poster())) is None)

am.CHANNEL_POST_HOUR = 0
am.set_setting("channel_chat", "")
check("расписание: без канала недельных постов нет",
      asyncio.run(am.post_weekly(Poster())) is None)
am.CHANNEL_DIGEST_DAY, am.CHANNEL_PROOF_DAY = "6", "2"

# --- спящий режим: ответ powercfg ---
# Проверка написана после того, как первая её версия вывалила на экран
# Traceback при каждом запуске бота: `text=True` заставляет Python читать
# ответ в кодировке системы, а на русской Windows он в другой. Падает при
# этом читающий поток subprocess, так что никакой try этого не ловит.
powercfg_eng = """Power Scheme GUID: 381b4222 (Balanced)
  Subgroup GUID: 238c9fa8 (Sleep)
    Power Setting GUID: 29f6c1db (Sleep after)
      Minimum Possible Setting: 0x00000000
      Maximum Possible Setting: 0xffffffff
      Possible Settings increment: 0x00000001
      Possible Settings units: Seconds
    Current AC Power Setting Index: 0x00000708
    Current DC Power Setting Index: 0x00000384"""
powercfg_rus = (powercfg_eng
                .replace("Current AC Power Setting Index",
                         "Индекс текущего параметра питания от сети переменного тока")
                .replace("Current DC Power Setting Index",
                         "Индекс текущего параметра питания от батареи"))

check("сон: время до сна разобрано", am.parse_powercfg(powercfg_eng) == 1800,
      am.parse_powercfg(powercfg_eng))
check("сон: русский ответ Windows понят так же",
      am.parse_powercfg(powercfg_rus) == 1800, am.parse_powercfg(powercfg_rus))
check("сон: отключённый сон - это ноль",
      am.parse_powercfg(powercfg_eng.replace("0x00000708", "0x00000000")) == 0)
check("сон: от батареи не путается с сетью",
      am.parse_powercfg(powercfg_eng) != 900)
check("сон: мусор вместо ответа не выдаётся за настройку",
      am.parse_powercfg("powercfg: неверные параметры") is None)
check("сон: пустой ответ не роняет проверку", am.parse_powercfg("") is None)
check("сон: не на Windows проверка честно отказывается",
      am.sleep_setting()[0] is None if os.name != "nt" else True)

# --- склонение по числу ---
forms = ("слово", "слова", "слов")
check("склонение: 1 слово", am.plural(1, *forms) == "слово")
check("склонение: 2 слова", am.plural(2, *forms) == "слова")
check("склонение: 5 слов", am.plural(5, *forms) == "слов")
check("склонение: 11 слов - исключение", am.plural(11, *forms) == "слов")
check("склонение: 14 слов - тоже исключение", am.plural(14, *forms) == "слов")
check("склонение: 21 слово, а не «21 слов»", am.plural(21, *forms) == "слово")
check("склонение: 51 слово", am.plural(51, *forms) == "слово")
check("склонение: 112 слов", am.plural(112, *forms) == "слов")

# Заглушки Telegram: ими пользуются и «Проверить всё», и «Проверить канал».
class Reply:
    def __init__(self):
        self.texts = []

    async def reply_text(self, text, **kwargs):
        self.texts.append(text)


class FakeUpdate:
    def __init__(self):
        self.message = Reply()


class Ctx:
    def __init__(self, bot):
        self.bot = bot


# --- кнопка «Проверить всё» ---
# Порознь каждая проверка отвечает «у меня всё хорошо», и молчащий бот при
# живых проверках - обычное дело: сломано то, чего никто не спрашивал.
class Member:
    def __init__(self, status, can_post=None):
        self.status = status
        if can_post is not None:
            self.can_post_messages = can_post


class ChannelBot:
    """Телеграм со стороны канала: кто мы в нём и что нам позволено.

    Права спрашиваются, а не проверяются отправкой: действие «печатает...»
    Telegram у каналов не принимает и у полноправного бота тоже, так что
    проверка через него врала бы на исправном канале.
    """
    id = 42

    def __init__(self, member=None, title="Находки Краснодара", missing=False):
        self.member, self.title, self.missing = member, title, missing

    async def get_chat(self, chat_id, **kwargs):
        if self.missing:
            raise RuntimeError("Bad Request: chat not found")
        return type("Chat", (), {"title": self.title})()

    async def get_chat_member(self, chat_id, user_id, **kwargs):
        return self.member


Silent = lambda: ChannelBot(Member("administrator", True))


real_fetch, real_sleep_check = am.fetch_html, am.sleep_setting


async def broken_fetch(url, source="avito"):
    raise am.AvitoBlocked("капча Qrator")


# Сон подменяем нарочно. Настоящая проверка спрашивает у Windows, как
# настроен компьютер, - и на машине владельца, где сон включён,
# «всё в порядке» никогда бы не совпало. Проверка, зависящая от настроек
# той машины, где её запустили, не проверка: она то зелёная, то красная
# без единой правки в коде.
am.sleep_setting = lambda: (True, "спящий режим от сети отключён")
am.fetch_html = broken_fetch
am.set_setting("enabled", "0")
am.set_setting("owner_chat", "")
am.set_setting("channel_chat", "@krd_nahodki")
u = FakeUpdate()
asyncio.run(am.cmd_selfcheck(u, Ctx(ChannelBot(Member("administrator", False)))))
report = u.message.texts[-1]
check("проверка всего: выключенный монитор замечен", "Монитор выключен" in report, report[:200])
check("проверка всего: капча названа прямо", "Авито не читается" in report, report[:250])
check("проверка всего: отобранное право публикации замечено",
      "нет права публиковать" in report, report[:400])
check("проверка всего: канал назван своим именем",
      "Находки Краснодара" in report, report[:400])

# Бот вовсе не в канале - беда другая, и совет должен быть другой.
u2 = FakeUpdate()
asyncio.run(am.cmd_selfcheck(u2, Ctx(ChannelBot(Member("left")))))
check("проверка всего: «бота нет в канале» отличается от «нет прав»",
      "бот не администратор" in u2.message.texts[-1], u2.message.texts[-1][:400])

# Канала нет совсем - тут ошибка Telegram, её и показываем.
u3 = FakeUpdate()
asyncio.run(am.cmd_selfcheck(u3, Ctx(ChannelBot(missing=True))))
check("проверка всего: несуществующий канал не выдаётся за исправный",
      "chat not found" in u3.message.texts[-1], u3.message.texts[-1][:400])
check("проверка всего: собран список, что чинить", "Что чинить" in report, report[-300:])
check("проверка всего: одна беда - один совет",
      report.count("\n1. ") == 1 and "4." in report, report[-300:])

# Всё в порядке: список бед должен исчезнуть совсем, а не остаться пустым
# заголовком - иначе владелец каждый раз ищет в нём подвох.
async def good_fetch(url, source="avito"):
    return html_json


am.fetch_html = good_fetch
am.set_setting("enabled", "1")
am.set_setting("owner_chat", "777")
am.set_setting("channel_chat", "")
u = FakeUpdate()
asyncio.run(am.cmd_selfcheck(u, Ctx(Silent())))
report = u.message.texts[-1]
check("проверка всего: живой Авито посчитан", "Авито читается" in report, report[:250])
check("проверка всего: когда всё цело - сказано прямо",
      "Всё в порядке" in report and "Что чинить" not in report, report[-200:])
check("проверка всего: показано время полного обхода",
      "Полный обход" in report and "слово" in report, report[-260:])

# Отдельно - что включённый сон замечен и попал в список бед. Это та беда,
# что однажды стоила суток работы, и молчать о ней нельзя.
am.sleep_setting = lambda: (False, "уснёт через 30 мин без дела")
u4 = FakeUpdate()
asyncio.run(am.cmd_selfcheck(u4, Ctx(Silent())))
check("проверка всего: включённый сон замечен",
      "уснёт через 30 мин" in u4.message.texts[-1], u4.message.texts[-1][:400])
check("проверка всего: про сон сказано, что чинить",
      "спящий режим" in u4.message.texts[-1] and "Что чинить" in u4.message.texts[-1],
      u4.message.texts[-1][-300:])

am.fetch_html, am.sleep_setting = real_fetch, real_sleep_check
am.set_setting("enabled", "0")
am.set_setting("channel_chat", "")

# --- кнопка «Проверить канал» ---
# Забытое право на публикацию - самая частая беда с каналами, и узнать о
# ней было неоткуда: находка молча ложилась в очередь, отказ уходил в лог.
# Кнопка должна называть причину по-человечески, а не пересказывать
# английский текст Telegram.
class Refuser:
    def __init__(self, error):
        self.error = error
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        if self.error:
            raise RuntimeError(self.error)
        self.sent.append((chat_id, text))
        return object()


am.set_setting("channel_chat", "")
u = FakeUpdate()
asyncio.run(am.cmd_channel_test(u, Ctx(Refuser(None))))
check("кнопка канала: без настройки объясняет, что вписать",
      "не настроен" in u.message.texts[0] and "Настроить бота" in u.message.texts[0], u.message.texts[0][:60])

am.set_setting("channel_chat", "@krd_nahodki")
u = FakeUpdate()
bot = Refuser(None)
asyncio.run(am.cmd_channel_test(u, Ctx(bot)))
check("кнопка канала: при успехе шлёт проверку в сам канал",
      bot.sent and bot.sent[0][0] == "@krd_nahodki", bot.sent)
check("кнопка канала: при успехе показывает отставание и порог",
      "✅" in u.message.texts[0] and "мин после тебя" in u.message.texts[0], u.message.texts[0][:80])

cases = [
    ("Forbidden: bot is not a member of the channel chat", "Добавить администратора"),
    ("Bad Request: chat not found", "-1001234567890"),
    ("Bad Request: not enough rights to send text messages to the chat",
     "Публикация сообщений"),
]
for error, expected in cases:
    u = FakeUpdate()
    asyncio.run(am.cmd_channel_test(u, Ctx(Refuser(error))))
    check(f"кнопка канала: «{error[:28]}...» объяснено по-человечески",
          "❌" in u.message.texts[0] and expected in u.message.texts[0], u.message.texts[0][:120])

am.set_setting("channel_chat", "")

# --- очередь поисковых слов ---
# Гнать все слова подряд - вернейший способ получить капчу, ею и кончилось
# 06.08.2026. Проверяем, что горстями обходятся все слова и без пропусков.
am.QUERIES_PER_CYCLE = 4
total = len(am.all_queries())
am._query_cursor = 0
seen = []
for _ in range((total + 3) // 4):
    seen.extend(am.take_queries())
check("очередь: за круг берётся ровно горсть", len(am.take_queries()) == 4)
check("очередь: за несколько кругов обходятся все слова",
      set(seen) == set(am.all_queries()), f"{len(set(seen))} из {total}")

am._query_cursor = total - 2
tail = am.take_queries()
check("очередь: с конца списка добирает с начала", len(tail) == 4 and len(set(tail)) == 4, tail)

am.QUERIES_PER_CYCLE = 0
check("очередь: ноль означает все слова разом", len(am.take_queries()) == total)
am.QUERIES_PER_CYCLE = 4

# --- настройки ---
am.set_setting("owner_chat", 12345)
check("настройки: чат владельца сохранён", am.get_owner_chat() == 12345)
check("настройки: по умолчанию выключен", not am.is_enabled())
am.set_setting("enabled", "1")
check("настройки: включение работает", am.is_enabled())

# Запуск без кнопок: пока опрос Telegram нестабилен, нажать «🟢 Включить»
# может не получиться, и монитор должен подниматься из .env.
am.set_setting("enabled", "0")
check("настройки: без базы и без .env монитор выключен", not am.is_enabled())
os.environ["AVITO_ENABLED"] = "1"
check("настройки: AVITO_ENABLED включает монитор мимо базы", am.is_enabled())
os.environ["AVITO_ENABLED"] = ""
check("настройки: пустой AVITO_ENABLED ничего не включает", not am.is_enabled())

os.environ["AVITO_OWNER_CHAT"] = "777"
am.set_setting("owner_chat", "")
check("настройки: чат владельца берётся из .env, если в базе пусто",
      am.get_owner_chat() == 777, am.get_owner_chat())

print("\n" + ("ВСЁ ЗЕЛЁНОЕ" if not fails else f"ПРОВАЛЕНО: {fails}"))
try:
    os.unlink(os.environ["AVITO_DB"])
except OSError:
    # На Windows файл базы остаётся занятым, и уборка падает уже после
    # «ВСЁ ЗЕЛЁНОЕ» - выглядит как провал проверок, хотя это просто мусор
    # во временной папке.
    pass
sys.exit(1 if fails else 0)
