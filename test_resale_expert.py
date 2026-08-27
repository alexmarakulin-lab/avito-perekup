# -*- coding: utf-8 -*-
"""Оффлайн-проверка консультанта: рыночный контекст, история, отказы. Сеть не нужна."""
import asyncio
import os
import sys
import tempfile
import time

os.environ["AVITO_DB"] = tempfile.mktemp(suffix=".db")
os.environ.pop("GROQ_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import avito_monitor
import resale_expert

fails = []


def check(name, cond, extra=""):
    print(("  OK  " if cond else " FAIL ") + name + (f"  <- {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# --- работа без ключа ---
check("без ключа: консультант помечен выключенным", not resale_expert.available())
answer = asyncio.run(resale_expert.ask(1, "перфоратор за 3500, брать?"))
check("без ключа: внятный отказ вместо падения", "GROQ_API_KEY" in answer, answer[:80])

# --- рыночный контекст ---
avito_monitor.init_db()
check("контекст: пустая база не даёт блок данных",
      resale_expert.market_context("перфоратор за 3500") == "")

# набиваем статистику: 10 перфораторов по 4000
avito_monitor.save_items("instrument", "перфоратор", [
    {"item_id": f"p{i}", "title": f"Перфоратор рабочий {i}", "price": 4000,
     "url": f"https://www.avito.ru/krasnodar/p{i}", "address": ""} for i in range(10)
])

ctx = resale_expert.market_context("перфоратор за 3500, стоит брать?")
check("контекст: подставляется медиана по теме вопроса", "4000" in ctx, ctx[:120])
check("контекст: указан размер выборки", "наблюдений 10" in ctx, ctx[:160])
check("контекст: помечен как краснодарский", "Краснодар" in ctx)
check("контекст: не лезет в чужую тему",
      resale_expert.market_context("сколько стоит холодильник") == "",
      resale_expert.market_context("сколько стоит холодильник")[:80])

# многословный ключевик должен ловиться по значимому слову
avito_monitor.save_items("climate", "сплит система", [
    {"item_id": f"s{i}", "title": f"Сплит система рабочая {i}", "price": 3000,
     "url": f"https://www.avito.ru/krasnodar/s{i}", "address": ""} for i in range(10)
])
ctx2 = resale_expert.market_context("почём сейчас сплит бу")
check("контекст: ловит многословный ключевик по слову «сплит»", "3000" in ctx2, ctx2[:120])

# --- проданное попадает в контекст ---
with avito_monitor._connect() as conn:
    conn.execute("UPDATE items SET sold_at = ? WHERE item_id = 'p1'", (time.time(),))
ctx3 = resale_expert.market_context("перфоратор почём")
check("контекст: показывает реально проданное", "Реально проданное" in ctx3, ctx3[-160:])

# --- история диалога ---
resale_expert._history.clear()
resale_expert._history[5] = [{"role": "user", "content": f"вопрос {i}"} for i in range(10)]
resale_expert.reset(5)
check("история: сброс очищает диалог", 5 not in resale_expert._history)

# --- защита от спама ---
resale_expert._last_call.clear()
check("антиспам: первый вопрос проходит", not resale_expert.rate_limited(42))
check("антиспам: второй подряд притормаживается", resale_expert.rate_limited(42))
check("антиспам: другому пользователю не мешает", not resale_expert.rate_limited(43))

# --- живой разговор с Groq, подделанный целиком ---
# Живого ответа от Groq в Telegram так и не видели, и всё, что лежит между
# вопросом и ответом - сборка запроса, перебор моделей, разбор ответа -
# до сих пор не проверялось ничем. Сеть здесь по-прежнему не нужна:
# httpx умеет отвечать сам, MockTransport'ом.
import contextlib
import json as _json

import httpx


@contextlib.contextmanager
def groq_answers(handler):
    """Подставляет консультанту поддельный Groq и настоящий ключ."""
    # Настоящий класс надо забрать до подмены: подменяется он у самого
    # httpx, и factory, позвав httpx.AsyncClient, позвала бы саму себя.
    real_client, real_key = resale_expert.httpx.AsyncClient, resale_expert.GROQ_API_KEY
    real_delay = resale_expert.RETRY_DELAY

    def factory(**kwargs):
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    resale_expert.httpx.AsyncClient = factory
    resale_expert.GROQ_API_KEY = "gsk_TESTKEY0123456789"   # только латиница: заголовок HTTP кириллицу не переживёт
    resale_expert.RETRY_DELAY = 0     # иначе проверка отказов ждала бы минуту
    try:
        yield
    finally:
        resale_expert.httpx.AsyncClient = real_client
        resale_expert.GROQ_API_KEY = real_key
        resale_expert.RETRY_DELAY = real_delay


def reply(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


seen = []


def ok_handler(request):
    seen.append(_json.loads(request.content))
    return reply("Бери за 2500, уйдёт за 6000, на руки 3000.")


resale_expert.reset(7)
with groq_answers(ok_handler):
    answer = asyncio.run(resale_expert.ask(7, "перфоратор за 3500, брать?"))
check("Groq: ответ доходит до владельца целиком", "на руки 3000" in answer, answer[:80])
check("Groq: ключ уходит заголовком", len(seen) == 1, len(seen))
sent = seen[0]
check("Groq: спрашивается первая модель списка",
      sent["model"] == resale_expert.GROQ_MODELS[0], sent["model"])
check("Groq: вопрос дошёл дословно",
      sent["messages"][-1]["content"] == "перфоратор за 3500, брать?")
check("Groq: рыночный блок вложен в системную часть",
      "4000" in sent["messages"][0]["content"], sent["messages"][0]["content"][-200:])
check("Groq: ответ лёг в историю - следующий вопрос будет с памятью",
      resale_expert._history[7][-1]["role"] == "assistant")

# Модели Groq снимают с обслуживания без предупреждения - на этот случай в
# списке их три. Проверяем, что перебор и правда доходит до следующей, а не
# упирается в первую же.
tried = []


def dead_first(request):
    model = _json.loads(request.content)["model"]
    tried.append(model)
    if model == resale_expert.GROQ_MODELS[0]:
        return httpx.Response(404, json={"error": {"message": "model_decommissioned"}})
    return reply("Ответ от запасной модели.")


resale_expert.reset(8)
with groq_answers(dead_first):
    answer = asyncio.run(resale_expert.ask(8, "перфоратор за 3500, брать?"))
check("Groq: снятая с обслуживания модель не роняет ответ",
      "запасной модели" in answer, answer[:80])
check("Groq: на снятой модели не топчемся, а идём к следующей",
      tried == [resale_expert.GROQ_MODELS[0], resale_expert.GROQ_MODELS[1]], tried)

# Ключ несколько раз попадал в переписку и на скриншоты, так что отказ по
# ключу - случай не выдуманный. Владельцу надо сказать прямо, что чинить.
resale_expert.reset(9)
with groq_answers(lambda r: httpx.Response(401, json={"error": {"message": "invalid_api_key"}})):
    answer = asyncio.run(resale_expert.ask(9, "перфоратор за 3500, брать?"))
check("Groq: негодный ключ назван негодным ключом",
      "GROQ_API_KEY" in answer and "не принимает" in answer, answer[:80])

resale_expert.reset(10)
with groq_answers(lambda r: httpx.Response(503, text="upstream down")):
    answer = asyncio.run(resale_expert.ask(10, "перфоратор за 3500, брать?"))
check("Groq: полный отказ - внятная просьба подождать, а не срыв",
      "не отвечает" in answer, answer[:80])

# Память диалога не должна расти без предела: шесть сообщений - потолок.
resale_expert.reset(11)
with groq_answers(ok_handler):
    for i in range(6):
        asyncio.run(resale_expert.ask(11, f"вопрос {i}"))
check("Groq: история не разрастается сверх потолка",
      len(resale_expert._history[11]) <= resale_expert.MAX_HISTORY,
      len(resale_expert._history[11]))

# --- нарезка длинных ответов ---
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["AVITO_BOT_TOKEN"] = "123:AAFAKE"
import avito_bot

check("нарезка: короткий текст не трогается", avito_bot.split_message("коротко") == ["коротко"])
long_text = "\n".join(f"строка номер {i} с текстом" for i in range(500))
parts = avito_bot.split_message(long_text)
check("нарезка: длинный текст разбит", len(parts) > 1, len(parts))
check("нарезка: каждый кусок в лимите телеграма", all(len(p) <= 4000 for p in parts),
      max(len(p) for p in parts))
check("нарезка: ничего не потеряно",
      sum(len(p.split("\n")) for p in parts) == 500,
      sum(len(p.split("\n")) for p in parts))

print("\n" + ("ВСЁ ЗЕЛЁНОЕ" if not fails else f"ПРОВАЛЕНО: {fails}"))
try:
    os.path.exists(os.environ["AVITO_DB"]) and os.unlink(os.environ["AVITO_DB"])
except OSError:
    pass   # на Windows файл базы остаётся занятым, это не провал проверок
sys.exit(1 if fails else 0)
