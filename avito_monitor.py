# -*- coding: utf-8 -*-
"""
Монитор Авито для перекупа: следит за новыми объявлениями в Краснодаре,
копит историю цен и присылает в Telegram только то, что дешевле рынка.

Запускается фоновой задачей внутри avito_bot.py, отдельный процесс не нужен.
"""
import asyncio
import json
import logging
import os
import random
import re
import sqlite3
import statistics
import time
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
from urllib.parse import quote, unquote

import httpx

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

logger = logging.getLogger(__name__)

# ========== НАСТРОЙКИ МОНИТОРА ==========
AVITO_REGION = os.getenv("AVITO_REGION", "krasnodar")
DB_PATH = os.getenv("AVITO_DB", "avito.db")

# Потолок закупки на один лот. Дороже - не показываем вообще.
MAX_PRICE = int(os.getenv("AVITO_MAX_PRICE", "5000"))
# Совсем дешёвое чаще всего мусор или развод - отсекаем снизу.
MIN_PRICE = int(os.getenv("AVITO_MIN_PRICE", "300"))

# Пауза между запросами к Авито. Меньше 6 секунд - быстро ловим блокировку.
REQ_DELAY_MIN = float(os.getenv("AVITO_REQ_DELAY_MIN", "8"))
REQ_DELAY_MAX = float(os.getenv("AVITO_REQ_DELAY_MAX", "16"))
# Пауза между полными кругами по всем категориям.
CYCLE_PAUSE = int(os.getenv("AVITO_CYCLE_PAUSE", "120"))

# Цена считается вкусной, если она не выше этой доли от медианы рынка.
DEAL_RATIO = float(os.getenv("AVITO_DEAL_RATIO", "0.6"))
# Медиане можно верить начиная с этого количества наблюдений.
MIN_STATS_SAMPLE = 8
# Глубина истории для расчёта медианы, дней.
STATS_WINDOW_DAYS = 21

# Час отправки суточного отчёта (по времени сервера).
DIGEST_HOUR = int(os.getenv("AVITO_DIGEST_HOUR", "21"))

# Сколько ранее найденных лотов перепроверять в сутки на предмет "продано".
SOLD_CHECK_LIMIT = 40

# Прокси для запросов к Авито, если понадобится: http://user:pass@host:port
AVITO_PROXY = os.getenv("AVITO_PROXY", "") or None

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# ========== КАТЕГОРИИ ==========
# Ключевые слова подобраны так, чтобы ловить ходовое и ликвидное,
# а не всё подряд. Правится руками без перезапуска логики.
CATEGORIES = {
    "instrument": {
        "name": "Инструмент и стройка",
        "emoji": "🔨",
        "queries": [
            "перфоратор",
            "шуруповерт аккумуляторный",
            "болгарка ушм",
            "лазерный уровень",
            "сварочный инвертор",
            "штроборез",
        ],
    },
    "garden": {
        "name": "Электро- и садовая техника",
        "emoji": "🌿",
        "queries": [
            "мотоблок",
            "триммер бензиновый",
            "бензопила",
            "насос погружной",
            "культиватор",
        ],
    },
    "climate": {
        "name": "Климат: кондиционеры",
        "emoji": "❄️",
        "queries": [
            "сплит система",
            "кондиционер бу",
            "внешний блок кондиционера",
        ],
    },
    "home": {
        "name": "Мебель и бытовая техника",
        "emoji": "🛋",
        "queries": [
            "стиральная машина",
            "холодильник",
            "диван",
            "шкаф купе",
            "микроволновка",
        ],
    },
}

# Слова-маркеры мусора: запчасти, нерабочее, объявления о покупке и услугах.
# Голое слово "ремонт" сюда сознательно не попало: "продаю после ремонта" -
# самая частая формулировка у нормальных объявлений с мебелью и техникой.
STOP_WORDS = [
    "на запчасти", "запчасти", "не работает", "нерабоч", "куплю", "приму в дар",
    "требует ремонта", "в ремонт", "на ремонт", "услуги", "аренда", "прокат",
    "под восстановление", "на разбор", "на детали", "битый", "сломан",
]


class AvitoBlocked(Exception):
    """Авито ответил капчей, 429 или заблокировал IP."""


# ========== БАЗА ==========
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                item_id     TEXT PRIMARY KEY,
                category    TEXT NOT NULL,
                query       TEXT NOT NULL,
                title       TEXT NOT NULL,
                price       INTEGER NOT NULL,
                url         TEXT NOT NULL,
                address     TEXT,
                first_seen  REAL NOT NULL,
                alerted     INTEGER DEFAULT 0,
                sold_at     REAL,
                last_check  REAL
            );
            CREATE INDEX IF NOT EXISTS idx_items_cat  ON items(category, first_seen);
            CREATE INDEX IF NOT EXISTS idx_items_q    ON items(query, first_seen);

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
    logger.info(f"Монитор Авито: база готова ({DB_PATH})")


def get_setting(key: str, default=None):
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def is_enabled() -> bool:
    """Включён ли монитор.

    Кнопка «🟢 Включить» пишет признак в базу, но нажать её можно только
    когда опрос Telegram жив, а он на этом сервере отваливается. Поэтому
    есть второй путь: AVITO_ENABLED=1 в .env. Монитор поднимется сразу при
    старте, независимо от того, доходят кнопки или нет.
    """
    if os.getenv("AVITO_ENABLED", "").strip() in ("1", "true", "yes"):
        return True
    return get_setting("enabled", "0") == "1"


def get_owner_chat() -> int | None:
    """Куда слать находки. Из базы, а если там пусто - из .env."""
    raw = get_setting("owner_chat") or os.getenv("AVITO_OWNER_CHAT", "")
    return int(raw) if raw else None


# ========== ЗАПРОС К АВИТО ==========
# Печенье Qrator живёт здесь и переживает отдельные запросы. Когда защита
# отдаёт капчу, она вместе с ней выдаёт метку _adcc - пропуск, который
# браузер предъявляет при следующем заходе. Поэтому человек видит капчу
# один раз, а дальше ходит свободно.
#
# Раньше каждый запрос открывался с чистого листа и метку выбрасывал. Для
# защиты это выглядело как бесконечная вереница незнакомцев, и капча
# доставалась каждому заново.
#
# Тип здесь принципиален. Получив httpx.Cookies, библиотека делает копию и
# складывает новые метки в неё - наружу они не возвращаются, и общая
# коробка остаётся пустой навсегда. Получив CookieJar, она берёт саму
# банку по ссылке. Разница незаметна глазом и стоила одного круга отладки.
COOKIES = CookieJar()

# Личина выбирается один раз при запуске и дальше не меняется. Браузер не
# перепредставляется на каждой странице, и бот не должен: одна метка плюс
# один и тот же User-Agent выглядят как один посетитель, а метка от одного
# в паре с именем другого - как подделка.
SESSION_UA = random.choice(USER_AGENTS)

# Сколько раз повторить запрос, упёршийся в капчу, и сколько ждать между
# попытками. Первый заход часто отдаёт капчу и выдаёт метку, второй с этой
# меткой уже проходит - ровно как у браузера.
RETRY_ATTEMPTS = int(os.getenv("AVITO_RETRY_ATTEMPTS", "3"))
RETRY_DELAY = float(os.getenv("AVITO_RETRY_DELAY", "8"))


def _headers() -> dict:
    return {
        "User-Agent": SESSION_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
        # br (brotli) сознательно не просим: httpx умеет распаковывать только
        # gzip и deflate. Если попросить br и получить его, вместо страницы в
        # resp.text окажется двоичный мусор - парсер найдёт ноль карточек, и
        # выглядеть это будет как "Авито сменил вёрстку". Ошибки при этом не
        # будет ни одной, поэтому искать причину пришлось бы долго.
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }


def build_search_url(query: str) -> str:
    # s=104 - сортировка "сначала новые", это ключевое: свежие лоты живут минуты.
    return (
        f"https://www.avito.ru/{AVITO_REGION}"
        f"?q={quote(query)}&pmax={MAX_PRICE}&pmin={MIN_PRICE}"
        f"&s=104&localPriority=1"
    )


async def fetch_html(url: str) -> str:
    """Забирает страницу Авито, переживая капчу Qrator.

    Капча с первого захода - обычное дело, а не приговор: вместе с ней
    приходит метка, и следующая попытка с этой меткой обычно проходит.
    Поэтому упираться в капчу сразу не надо, надо зайти ещё раз.
    """
    last = ""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        async with httpx.AsyncClient(
            headers=_headers(), timeout=30, follow_redirects=True,
            proxy=AVITO_PROXY, cookies=COOKIES,
        ) as client:
            resp = await client.get(url)

        low = resp.text.lower()
        # Заслон Qrator приходит с кодом 429, но это не "частим запросами":
        # ни паузы, ни смена заголовков тут не помогают, помогает метка.
        wall = ("доступ ограничен" in low or "firewall" in low
                or "подтвердите, что вы не робот" in low)
        if wall:
            last = f"капча Qrator, попыток {attempt}"
            logger.debug(f"Монитор Авито: капча на попытке {attempt}, беру метку и повторяю")
            if attempt < RETRY_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY * attempt)
                continue
            raise AvitoBlocked(last)

        if resp.status_code in (403, 429):
            raise AvitoBlocked(f"HTTP {resp.status_code} - слишком часто, нужны паузы длиннее")
        resp.raise_for_status()
        return resp.text

    raise AvitoBlocked(last or "выдача не пришла")


# ========== ПАРСИНГ ==========
def _parse_price(value) -> int:
    """Из '4 500 ₽' или 4500 делает 4500. Ноль - значит цену вытащить не смогли."""
    if isinstance(value, (int, float)):
        return int(value)
    if not value:
        return 0
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else 0


def _walk_json_items(node, out: list, depth: int = 0):
    """Рекурсивно ищет в JSON выдачи словари, похожие на карточку объявления.

    Обход именно рекурсивный, а не по фиксированному пути: Авито регулярно
    перекладывает структуру, и жёсткий путь ломается на первом же релизе.
    """
    if depth > 12 or len(out) > 200:
        return
    if isinstance(node, dict):
        has_id = "id" in node
        has_title = "title" in node
        has_url = "urlPath" in node or "url" in node
        if has_id and has_title and has_url:
            out.append(node)
        else:
            for value in node.values():
                _walk_json_items(value, out, depth + 1)
    elif isinstance(node, list):
        for value in node:
            _walk_json_items(value, out, depth + 1)


# Авито кладёт данные выдачи в переменную на странице. Имя переменной со
# временем менялось: было __initialData__, стало __preloadedState__ - ищем
# оба, чтобы не остаться без разбора при очередном переезде.
#
# Значение - строка в кавычках, и внутри неё кавычки экранированы обратной
# косой. Поэтому «до ближайшей кавычки» брать нельзя: строка оборвётся на
# первой же \" внутри. Шаблон ниже честно проходит экранированные пары.
STATE_VAR_RE = re.compile(
    r'window\.__(?:preloadedState|initialData)__\s*=\s*("(?:\\.|[^"\\])*")',
    re.DOTALL,
)


def parse_from_json(html: str) -> list:
    """Основная стратегия: JSON, который Авито встраивает в страницу."""
    match = STATE_VAR_RE.search(html)
    if not match:
        return []
    try:
        # Первый json.loads снимает кавычки и экранирование - остаётся текст
        # самого JSON. Старый __initialData__ был вдобавок url-кодирован,
        # у него текст начинается с процента, а не с фигурной скобки.
        text = json.loads(match.group(1))
        if not text.lstrip().startswith(("{", "[")):
            text = unquote(text)
        data = json.loads(text)
    except (ValueError, UnicodeDecodeError) as exc:
        logger.debug(f"Монитор Авито: JSON не разобрался ({exc})")
        return []

    raw_items: list = []
    _walk_json_items(data, raw_items)

    items = []
    for raw in raw_items:
        price = _parse_price(
            (raw.get("priceDetailed") or {}).get("value")
            if isinstance(raw.get("priceDetailed"), dict)
            else raw.get("price")
        )
        url_path = raw.get("urlPath") or raw.get("url") or ""
        if not url_path:
            continue
        items.append({
            "item_id": str(raw.get("id")),
            "title": str(raw.get("title", "")).strip(),
            "price": price,
            "url": url_path if url_path.startswith("http") else f"https://www.avito.ru{url_path}",
            "address": (raw.get("geo") or {}).get("formattedAddress", "")
            if isinstance(raw.get("geo"), dict) else "",
        })
    return items


def parse_from_dom(html: str) -> list:
    """Запасная стратегия: разбор разметки по data-marker."""
    if not BS4_AVAILABLE:
        return []
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for block in soup.select('[data-marker="item"]'):
        item_id = block.get("data-item-id") or block.get("id", "").replace("i", "")
        title_el = block.select_one('[data-marker="item-title"]')
        if not item_id or not title_el:
            continue

        price_el = block.select_one('[itemprop="price"]')
        price = _parse_price(price_el.get("content") if price_el else None)
        if not price:
            price_text = block.select_one('[data-marker="item-price"]')
            price = _parse_price(price_text.get_text() if price_text else None)

        href = title_el.get("href", "")
        addr_el = block.select_one('[class*="geo-address"], [data-marker="item-address"]')
        items.append({
            "item_id": str(item_id),
            "title": title_el.get_text(strip=True),
            "price": price,
            "url": href if href.startswith("http") else f"https://www.avito.ru{href}",
            "address": addr_el.get_text(strip=True) if addr_el else "",
        })
    return items


def parse_search(html: str) -> list:
    """JSON сначала, разметка - как страховка. Пустой список = вёрстка сменилась."""
    items = parse_from_json(html)
    source = "json"
    if not items:
        items = parse_from_dom(html)
        source = "dom"
    if items:
        logger.debug(f"Монитор Авито: разобрано {len(items)} карточек ({source})")
    return items


def is_junk(title: str) -> bool:
    low = title.lower()
    return any(word in low for word in STOP_WORDS)


# ========== СТАТИСТИКА ==========
def median_price(query: str) -> tuple[int | None, int]:
    """Медиана цены по ключевому слову за окно наблюдения и размер выборки."""
    since = time.time() - STATS_WINDOW_DAYS * 86400
    with _connect() as conn:
        rows = conn.execute(
            "SELECT price FROM items WHERE query = ? AND first_seen > ? AND price > 0",
            (query, since),
        ).fetchall()
    prices = [r["price"] for r in rows]
    if len(prices) < MIN_STATS_SAMPLE:
        return None, len(prices)
    return int(statistics.median(prices)), len(prices)


def save_items(category: str, query: str, items: list) -> list:
    """Пишет карточки в базу и возвращает только те, которых раньше не видели.

    Мусор (запчасти, нерабочее, объявления о покупке) в базу не попадает:
    иначе лоты по 900 ₽ утянут медиану вниз и настоящие находки перестанут
    выглядеть дешёвыми.
    """
    fresh = []
    now = time.time()
    with _connect() as conn:
        for item in items:
            if not item["item_id"] or not item["price"]:
                continue
            if is_junk(item["title"]):
                continue
            exists = conn.execute(
                "SELECT 1 FROM items WHERE item_id = ?", (item["item_id"],)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO items(item_id, category, query, title, price, url, address, first_seen)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (item["item_id"], category, query, item["title"], item["price"],
                 item["url"], item.get("address", ""), now),
            )
            fresh.append(item)
    return fresh


def rate_deal(item: dict, query: str) -> tuple[bool, str]:
    """Решает, стоит ли будить владельца из-за этого лота."""
    price = item["price"]
    if price > MAX_PRICE or price < MIN_PRICE:
        return False, ""
    if is_junk(item["title"]):
        return False, ""

    median, sample = median_price(query)
    if median is None:
        # Статистики ещё нет - первые дни шлём всё в рамках бюджета,
        # заодно так быстрее набирается выборка.
        return True, f"статистика копится ({sample} шт.)"

    ratio = price / median
    if ratio <= DEAL_RATIO:
        return True, f"🔥 −{int((1 - ratio) * 100)}% к медиане {median:,} ₽".replace(",", " ")
    if ratio <= 0.8:
        return True, f"ниже рынка на {int((1 - ratio) * 100)}% (медиана {median:,} ₽)".replace(",", " ")
    return False, ""


# ========== ОТПРАВКА ==========
async def send_alert(bot, chat_id: int, item: dict, category: str, note: str):
    cat = CATEGORIES.get(category, {})
    text = (
        f"{cat.get('emoji', '📦')} <b>{cat.get('name', category)}</b>\n\n"
        f"{item['title']}\n"
        f"<b>{item['price']:,} ₽</b>".replace(",", " ") + "\n"
    )
    if item.get("address"):
        text += f"📍 {item['address']}\n"
    if note:
        text += f"\n{note}\n"
    text += f"\n{item['url']}"

    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML",
                           disable_web_page_preview=False)
    with _connect() as conn:
        conn.execute("UPDATE items SET alerted = 1 WHERE item_id = ?", (item["item_id"],))


def build_report(hours: int = 24) -> str:
    """Суточная сводка: сколько появилось, почём и что реально продалось."""
    since = time.time() - hours * 3600
    lines = [f"📊 <b>Отчёт по рынку за {hours} ч</b>\n"]

    with _connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) c FROM items WHERE first_seen > ?", (since,)
        ).fetchone()["c"]

        for key, cat in CATEGORIES.items():
            rows = conn.execute(
                "SELECT price, title FROM items WHERE category = ? AND first_seen > ? AND price > 0",
                (key, since),
            ).fetchall()
            if not rows:
                continue
            prices = sorted(r["price"] for r in rows)
            cheapest = rows[[r["price"] for r in rows].index(prices[0])]
            lines.append(
                f"{cat['emoji']} <b>{cat['name']}</b>\n"
                f"   новых: {len(prices)} | медиана: {int(statistics.median(prices)):,} ₽"
                f" | мин: {prices[0]:,} ₽".replace(",", " ")
            )
            lines.append(f"   дешевле всех: {cheapest['title'][:60]}")

        sold = conn.execute(
            "SELECT title, price FROM items WHERE sold_at > ? ORDER BY sold_at DESC LIMIT 5",
            (since,),
        ).fetchall()

    if sold:
        lines.append("\n<b>Ушло с рынка</b> (реальная цена продажи):")
        for row in sold:
            lines.append(f"   • {row['title'][:50]} — {row['price']:,} ₽".replace(",", " "))
    else:
        lines.append("\n<i>Продаж за период не зафиксировано.</i>")

    if not total:
        lines.append("\n⚠️ Ноль новых карточек за сутки — похоже, парсер не видит выдачу. "
                     "Проверь /avito_test.")
    return "\n".join(lines)


async def check_sold():
    """Перепроверяет старые лоты: снятое с публикации = проданное.

    Это единственный способ узнать реальную цену сделки, а не цену хотелки.
    """
    cutoff = time.time() - 6 * 3600
    with _connect() as conn:
        rows = conn.execute(
            "SELECT item_id, url FROM items "
            "WHERE sold_at IS NULL AND alerted = 1 AND first_seen < ? "
            "AND (last_check IS NULL OR last_check < ?) "
            "ORDER BY first_seen LIMIT ?",
            (cutoff, time.time() - 86400, SOLD_CHECK_LIMIT),
        ).fetchall()

    for row in rows:
        try:
            html = await fetch_html(row["url"])
            closed = ("снято с публикации" in html.lower()
                      or "объявление больше не доступно" in html.lower())
        except AvitoBlocked:
            logger.warning("Монитор Авито: блокировка при проверке продаж, пауза")
            return
        except Exception as exc:
            logger.debug(f"Монитор Авито: не проверил {row['item_id']} ({exc})")
            closed = False

        with _connect() as conn:
            if closed:
                conn.execute("UPDATE items SET sold_at = ?, last_check = ? WHERE item_id = ?",
                             (time.time(), time.time(), row["item_id"]))
            else:
                conn.execute("UPDATE items SET last_check = ? WHERE item_id = ?",
                             (time.time(), row["item_id"]))
        await asyncio.sleep(random.uniform(REQ_DELAY_MIN, REQ_DELAY_MAX))


# ========== ДИАГНОСТИКА ==========
async def self_test(query: str = "перфоратор") -> str:
    """Живая проверка: доходим ли до Авито и понимаем ли выдачу."""
    url = build_search_url(query)
    try:
        html = await fetch_html(url)
    except AvitoBlocked as exc:
        return (f"🚫 Авито блокирует запросы: {exc}\n\n"
                f"Лечится сменой IP или прокси (переменная AVITO_PROXY).")
    except Exception as exc:
        return f"❌ Не достучались до Авито: {type(exc).__name__}: {exc}"

    via_json = parse_from_json(html)
    via_dom = parse_from_dom(html)
    items = via_json or via_dom

    out = [
        f"🔍 Тест по запросу «{query}»",
        f"страница получена: {len(html):,} символов".replace(",", " "),
        f"через JSON: {len(via_json)} карточек",
        f"через разметку: {len(via_dom)} карточек" + ("" if BS4_AVAILABLE else " (bs4 не установлен)"),
        "",
    ]
    if not items:
        out.append("⚠️ Ноль карточек. Авито сменил вёрстку — нужно поправить парсер.")
    else:
        out.append("Первые находки:")
        for item in items[:5]:
            out.append(f"  • {item['title'][:55]} — {item['price']:,} ₽".replace(",", " "))
    return "\n".join(out)


# ========== ГЛАВНЫЙ ЦИКЛ ==========
async def monitor_loop(bot):
    """Крутится вечно рядом с ботом: обходит категории, шлёт находки и отчёт."""
    init_db()
    backoff = 0
    last_digest_day = None
    last_sold_check = 0.0

    logger.info("Монитор Авито: цикл запущен")

    while True:
        try:
            if not is_enabled():
                await asyncio.sleep(30)
                continue

            chat_id = get_owner_chat()
            if not chat_id:
                logger.warning("Монитор Авито: не задан чат владельца, жду /avito_on")
                await asyncio.sleep(60)
                continue

            found_total = 0
            for cat_key, cat in CATEGORIES.items():
                for query in cat["queries"]:
                    try:
                        html = await fetch_html(build_search_url(query))
                    except AvitoBlocked as exc:
                        backoff = min(backoff * 2 or 300, 3600)
                        logger.warning(f"Монитор Авито: {exc}. Пауза {backoff} с")
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"⚠️ Авито ограничил доступ ({exc}). "
                                 f"Пауза {backoff // 60} мин, потом продолжу.",
                        )
                        await asyncio.sleep(backoff)
                        continue
                    except Exception as exc:
                        logger.warning(f"Монитор Авито: запрос «{query}» упал ({exc})")
                        await asyncio.sleep(REQ_DELAY_MAX)
                        continue

                    backoff = 0
                    items = parse_search(html)
                    fresh = save_items(cat_key, query, items)
                    found_total += len(fresh)

                    for item in fresh:
                        good, note = rate_deal(item, query)
                        if not good:
                            continue
                        try:
                            await send_alert(bot, chat_id, item, cat_key, note)
                        except Exception as exc:
                            logger.error(f"Монитор Авито: не отправил алерт ({exc})")

                    await asyncio.sleep(random.uniform(REQ_DELAY_MIN, REQ_DELAY_MAX))

            logger.info(f"Монитор Авито: круг закрыт, новых карточек {found_total}")

            now = datetime.now()
            if now.hour == DIGEST_HOUR and last_digest_day != now.date():
                last_digest_day = now.date()
                try:
                    await bot.send_message(chat_id=chat_id, text=build_report(24),
                                           parse_mode="HTML")
                except Exception as exc:
                    logger.error(f"Монитор Авито: отчёт не ушёл ({exc})")

            if time.time() - last_sold_check > 86400:
                last_sold_check = time.time()
                await check_sold()

            await asyncio.sleep(CYCLE_PAUSE)

        except asyncio.CancelledError:
            logger.info("Монитор Авито: остановлен")
            raise
        except Exception as exc:
            logger.error(f"Монитор Авито: сбой цикла ({exc})", exc_info=True)
            await asyncio.sleep(120)


# ========== КОМАНДЫ TELEGRAM ==========
async def cmd_avito(update, context):
    """Статус монитора."""
    init_db()
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        day = conn.execute("SELECT COUNT(*) c FROM items WHERE first_seen > ?",
                           (time.time() - 86400,)).fetchone()["c"]
        sold = conn.execute("SELECT COUNT(*) c FROM items WHERE sold_at IS NOT NULL").fetchone()["c"]

    queries = sum(len(c["queries"]) for c in CATEGORIES.values())
    cycle_min = int((queries * (REQ_DELAY_MIN + REQ_DELAY_MAX) / 2 + CYCLE_PAUSE) / 60)

    await update.message.reply_text(
        f"{'🟢 Монитор работает' if is_enabled() else '⚪️ Монитор выключен'}\n\n"
        f"Регион: {AVITO_REGION}\n"
        f"Потолок закупки: {MAX_PRICE:,} ₽".replace(",", " ") + "\n"
        f"Категорий: {len(CATEGORIES)}, запросов: {queries}\n"
        f"Круг обхода: ~{cycle_min} мин\n\n"
        f"В базе: {total} объявлений\n"
        f"За сутки: {day}\n"
        f"Отслежено продаж: {sold}\n\n"
        "/avito_on — включить\n"
        "/avito_off — выключить\n"
        "/avito_report — отчёт сейчас\n"
        "/avito_test — проверить связь с Авито"
    )


async def cmd_avito_on(update, context):
    init_db()
    set_setting("owner_chat", update.effective_chat.id)
    set_setting("enabled", "1")
    await update.message.reply_text(
        "🟢 Монитор включён. Находки буду слать сюда.\n\n"
        "Первые пару дней шлю всё, что укладывается в бюджет — так копится "
        "статистика цен. Дальше останутся только лоты дешевле медианы рынка."
    )


async def cmd_avito_off(update, context):
    init_db()
    set_setting("enabled", "0")
    await update.message.reply_text("⚪️ Монитор выключен. База и история цен сохранены.")


async def cmd_avito_report(update, context):
    init_db()
    await update.message.reply_text(build_report(24), parse_mode="HTML")


async def cmd_avito_test(update, context):
    query = " ".join(context.args) if context.args else "перфоратор"
    await update.message.reply_text(f"⏳ Проверяю Авито по запросу «{query}»...")
    await update.message.reply_text(await self_test(query))


def register(app):
    """Подключает команды монитора и фоновую задачу к приложению бота."""
    from telegram.ext import CommandHandler

    app.add_handler(CommandHandler("avito", cmd_avito))
    app.add_handler(CommandHandler("avito_on", cmd_avito_on))
    app.add_handler(CommandHandler("avito_off", cmd_avito_off))
    app.add_handler(CommandHandler("avito_report", cmd_avito_report))
    app.add_handler(CommandHandler("avito_test", cmd_avito_test))

    previous_post_init = getattr(app, "post_init", None)

    async def _start_monitor(application):
        if previous_post_init:
            await previous_post_init(application)
        init_db()
        application.create_task(monitor_loop(application.bot))

    app.post_init = _start_monitor
    logger.info("Монитор Авито: команды подключены")
