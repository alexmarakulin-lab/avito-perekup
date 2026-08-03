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
os.path.exists(os.environ["AVITO_DB"]) and os.unlink(os.environ["AVITO_DB"])
sys.exit(1 if fails else 0)
