# -*- coding: utf-8 -*-
"""
Консультант по перекупу: оценка лота, торг, проверка перед покупкой.

Отвечает через Groq, но опирается не только на модель: если по теме вопроса
в базе монитора есть накопленная статистика, она подставляется в промпт.
Так «сколько стоит перфоратор» превращается в ответ по твоему рынку, а не
по среднему по стране.
"""
import asyncio
import logging
import os
import time

import httpx

import avito_monitor

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Порядок перебора: если модель сняли с обслуживания, идём к следующей.
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
]

MAX_HISTORY = 6
MAX_TOKENS = 1024
RETRY_ATTEMPTS = 2
RETRY_DELAY = 3

SYSTEM_PROMPT = """Ты - практик с 15-летним опытом перекупа б/у техники и инструмента в Краснодаре. Отвечаешь как напарник по делу, коротко и по существу.

ПРАВИЛА:
1. Только конкретика: цифры, суммы, проценты, сроки. Никаких "может быть", "как правило", "зависит от многого".
2. Если вопрос простой - отвечай одним абзацем. Не раздувай списками.
3. Никогда не выдумывай точные рыночные цены. Если данных по рынку нет в блоке ДАННЫЕ - так и скажи и рассуждай в диапазонах.
4. Язык: русский. Формулы обычным текстом, без LaTeX.
5. Не пересказывай человеку то, что он сам написал.

ЧТО ТЫ УМЕЕШЬ:
- Оценить лот: сколько реально стоит, за сколько уйдёт, какая маржа
- Сказать, что проверить перед покупкой у конкретной техники и на чём чаще всего врут продавцы
- Подсказать, как торговаться: с какой суммы начинать и на какие недостатки давить
- Прикинуть стоимость восстановления и стоит ли оно возни
- Учесть сезон: в Краснодаре кондиционеры дорожают с апреля, садовая техника - с марта

КЛЮЧЕВАЯ АРИФМЕТИКА:
Закупка не дороже 30-35% от цены продажи. Всё, что выше, съедается бензином, временем на встречи и торгом. При марже меньше двух концов это не бизнес.

Если человек называет цену лота - всегда считай ему три числа: за сколько брать, за сколько продавать, что останется на руки после расходов."""

# Оперативная память диалога, по пользователям.
_history: dict = {}
_last_call: dict = {}


def available() -> bool:
    return bool(GROQ_API_KEY)


def market_context(question: str) -> str:
    """Подтягивает из базы монитора статистику по темам, упомянутым в вопросе.

    Без этого блока модель начнёт фантазировать цены - что для перекупа
    хуже, чем честное "данных нет".
    """
    low = question.lower()
    lines = []

    for cat in avito_monitor.CATEGORIES.values():
        for query in cat["queries"]:
            # Совпадение по любому значимому слову ключевика: "перфоратор" в
            # вопросе поймает запрос "перфоратор", а "сплит" - "сплит система".
            words = [w for w in query.split() if len(w) > 4]
            if not any(w in low for w in words):
                continue

            median, sample = avito_monitor.median_price(query)
            if median is None:
                continue
            lines.append(f"- «{query}»: медиана {median} руб, наблюдений {sample}")

    if not lines:
        return ""

    sold = _recent_sold()
    block = "ДАННЫЕ ПО ТВОЕМУ РЫНКУ (Краснодар, собраны монитором за 3 недели):\n" + "\n".join(lines)
    if sold:
        block += "\n\nРеально проданное (объявление снято):\n" + "\n".join(sold)
    block += "\n\nОпирайся на эти цифры, они точнее любых общих оценок."
    return block


def _recent_sold(limit: int = 5) -> list:
    try:
        with avito_monitor._connect() as conn:
            rows = conn.execute(
                "SELECT title, price FROM items WHERE sold_at IS NOT NULL "
                "ORDER BY sold_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [f"- {r['title'][:60]}: {r['price']} руб" for r in rows]
    except Exception as exc:
        logger.debug(f"Консультант: статистика продаж недоступна ({exc})")
        return []


def reset(user_id: int):
    _history.pop(user_id, None)


async def ask(user_id: int, question: str) -> str:
    """Задаёт вопрос модели с историей диалога и рыночным контекстом."""
    if not available():
        return ("Консультант выключен: не задан GROQ_API_KEY.\n\n"
                "Ключ бесплатно берётся на console.groq.com, "
                "вписывается в .env и подхватывается после перезапуска.")

    history = _history.setdefault(user_id, [])
    history.append({"role": "user", "content": question})
    if len(history) > MAX_HISTORY:
        del history[:-MAX_HISTORY]

    system = SYSTEM_PROMPT
    context = market_context(question)
    if context:
        system += "\n\n" + context

    messages = [{"role": "system", "content": system}] + history
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    last_error = None

    for model in GROQ_MODELS:
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": MAX_TOKENS,
                    "temperature": 0.4,
                }
                async with httpx.AsyncClient(timeout=40) as client:
                    resp = await client.post(GROQ_URL, headers=headers, json=payload)
                    resp.raise_for_status()
                    answer = resp.json()["choices"][0]["message"]["content"]

                history.append({"role": "assistant", "content": answer})
                logger.info(f"Консультант: ответ от {model}, рыночный блок: {bool(context)}")
                return answer

            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                logger.warning(f"Консультант: HTTP {status} на {model}, попытка {attempt}")
                if status in (401, 403):
                    return "Groq не принимает ключ. Проверь GROQ_API_KEY в .env."
                if status == 429:
                    await asyncio.sleep(RETRY_DELAY * (2 ** attempt))
                elif status in (404, 400):
                    break  # модель снята с обслуживания - сразу к следующей
                else:
                    await asyncio.sleep(RETRY_DELAY)

            except httpx.TimeoutException:
                last_error = Exception("таймаут")
                logger.warning(f"Консультант: таймаут на {model}, попытка {attempt}")
                await asyncio.sleep(RETRY_DELAY)

            except Exception as exc:
                last_error = exc
                logger.warning(f"Консультант: сбой на {model} ({exc})")
                await asyncio.sleep(RETRY_DELAY)

    logger.error(f"Консультант: все модели недоступны ({last_error})")
    return "Groq сейчас не отвечает. Попробуй через пару минут."


def rate_limited(user_id: int, seconds: int = 3) -> bool:
    """Простая защита от спама: не чаще одного вопроса в несколько секунд."""
    now = time.time()
    if now - _last_call.get(user_id, 0) < seconds:
        return True
    _last_call[user_id] = now
    return False
