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

# Qrator узнаёт клиента не только по заголовкам, но и по рукопожатию -
# по тому, как программа устанавливает защищённое соединение. У Chrome оно
# своё, у httpx своё, и заголовками эту разницу не замаскировать: клиент,
# который называется браузером, а здоровается как библиотека, приметнее
# честного робота. curl_cffi повторяет рукопожатие Chrome целиком.
try:
    from curl_cffi.requests import AsyncSession as CffiSession
    CFFI_AVAILABLE = True
except ImportError:
    CFFI_AVAILABLE = False

# ...но и рукопожатия оказалось мало: страницу дорисовывает javascript, а
# библиотека его не выполняет. Единственный способ, который Авито пропустил, -
# настоящий видимый браузер. Подробности и замеры - в avito_browser.py.
import avito_browser

logger = logging.getLogger(__name__)

# ========== НАСТРОЙКИ МОНИТОРА ==========
AVITO_REGION = os.getenv("AVITO_REGION", "krasnodar")
DB_PATH = os.getenv("AVITO_DB", "avito.db")

# Потолок закупки на один лот. Дороже - не показываем вообще.
MAX_PRICE = int(os.getenv("AVITO_MAX_PRICE", "5000"))
# Совсем дешёвое чаще всего мусор или развод - отсекаем снизу.
MIN_PRICE = int(os.getenv("AVITO_MIN_PRICE", "300"))

# Пауза между запросами к Авито.
#
# Умолчания подняты по горькому опыту 06.08.2026. Утром три обращения за всё
# время - приходила настоящая выдача. Днём около сорока за час - капча, и не
# отпустило до вечера. А прежние настройки (8-16 секунд, круг раз в две
# минуты) давали примерно 180 обращений в час круглосуточно, то есть вчетверо
# больше того, чем блокировка была заработана.
REQ_DELAY_MIN = float(os.getenv("AVITO_REQ_DELAY_MIN", "45"))
REQ_DELAY_MAX = float(os.getenv("AVITO_REQ_DELAY_MAX", "90"))
# Пауза между кругами.
CYCLE_PAUSE = int(os.getenv("AVITO_CYCLE_PAUSE", "900"))

# Сколько поисковых слов обходить за один круг. Ноль - все подряд, как было.
#
# Слов девятнадцать, и гнать их пачкой - вернейший способ снова попасть в
# капчу. Поэтому за круг берётся горсть, а список крутится по кольцу: за
# несколько кругов слова всё равно обойдутся все, просто вразвалку. Заодно
# это прямо экономит деньги, если Авито читается через прокси с оплатой за
# трафик: каждая страница выдачи весит без малого мегабайт.
QUERIES_PER_CYCLE = int(os.getenv("AVITO_QUERIES_PER_CYCLE", "3"))

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
    # У техники Apple своя вилка цен. Общие 300-5000 ₽ рассчитаны на
    # инструмент, и с ними айфон не нашёлся бы вообще: всё, что дешевле
    # потолка, - это запчасти, копии и разводы. Поэтому у категории свои
    # min_price и max_price, они перебивают общие.
    "iphone": {
        "name": "Айфоны",
        "emoji": "📱",
        # Ниже сорока тысяч рабочего 17 Pro не бывает - там битые, залитые,
        # корпуса и «айфон на запчасти». Верх - примерно рыночная цена б/у:
        # дороже искать нечего, выгоды там нет по определению.
        "min_price": 40000,
        "max_price": 95000,
        "queries": [
            "iphone 17 pro",
        ],
    },
    "applewatch": {
        "name": "Часы Apple",
        "emoji": "⌚",
        # Часы живут в куда более широком коридоре: старые серии уходят за
        # семь-восемь тысяч, свежие - за сорок.
        "min_price": 6000,
        "max_price": 45000,
        "queries": [
            "apple watch",
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
    # Услуги мастеров. На выдаче по «сплит система» их полно: «Ремонт
    # монтаж и обслуживание кондиционеров», «Чистка заправка» - и цена у них
    # своя, 500-3000 ₽ за работу, а не за товар. Товаром это не является,
    # а медиану тянет вниз сильнее всего остального.
    # Слово "монтаж" заодно ловит "демонтаж" - это тоже услуга.
    "монтаж", "обслуживание", "заправка", "чистка", "диагностика",
    # Подделки и витрины магазинов. На выдаче по айфонам их больше, чем
    # живых объявлений: копии за десять тысяч и магазинные карточки «в
    # рассрочку». Перекупу не годится ни то, ни другое, а медиану они
    # ломают в обе стороны сразу.
    #
    # "рассрочка" сюда попала сознательно, хотя изредка так пишут и в
    # честных объявлениях: на выдаче по технике это почти всегда магазин.
    "копия", "реплика", "муляж", "под заказ", "рассрочка",
]

# Слова короче трёх букв («бу», «и», «с») ничего не различают: они есть в
# половине заголовков, и в фильтре от них один вред.
MIN_WORD_LEN = 3

# Одно и то же название, написанное двумя алфавитами. Приводим к латинице,
# потому что в запросах она короче и однозначнее.
#
# Слово «про» сюда сознательно НЕ попало, хотя и просится: в русском это
# ещё и предлог. Превратив его в pro, мы получили бы совпадение на любом
# «продам про запас», а для попадания в фильтр хватает одного слова.
# Различают товар всё равно «айфон» и «вотч», а не «про».
SYNONYMS = {
    "айфон": "iphone", "айфона": "iphone", "айфоны": "iphone",
    "айфонов": "iphone", "айфоне": "iphone",
    "эппл": "apple", "эпл": "apple", "аппл": "apple",
    "вотч": "watch", "воч": "watch",
}
# Голого «часы» здесь тоже нет и быть не должно. Превратив его в watch, мы
# бы засчитали за Apple Watch любые часы - и Casio, и настенные: для
# совпадения хватает одного слова, а «watch» как раз им и стало бы.
# «Умные часы Эппл вотч» находятся и без этого, по слову «эппл».


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

# Чьё рукопожатие изображаем. Список личин ведёт сама библиотека, названия
# вида chrome, chrome124, safari. AVITO_HTTP=httpx возвращает старый способ,
# если новый вдруг окажется хуже - менять код для этого не придётся.
IMPERSONATE = os.getenv("AVITO_IMPERSONATE", "chrome")
USE_CFFI = CFFI_AVAILABLE and os.getenv("AVITO_HTTP", "cffi").lower() != "httpx"

# Чем ходим за страницей. По умолчанию браузером - это единственное, что
# Авито пропускает; замеры и разбор почему - в avito_browser.py. Прежний
# способ никуда не делся: AVITO_FETCH=http вернёт запросы по http, если
# защита однажды подобреет или страницы понадобятся быстрее и дешевле.
USE_BROWSER = os.getenv("AVITO_FETCH", "browser").strip().lower() == "browser"

# Разговор с Авито ведётся одной и той же сессией: в ней живут метки Qrator
# и уже установленные соединения. Новая сессия на каждый запрос - это опять
# незнакомец с улицы, со всеми вытекающими.
_cffi_session = None

# След последнего обращения: по попытке на строку. Нужен, чтобы неудачный
# заход тоже приносил сведения, а не одно слово «не пришло».
LAST_TRACE: list[str] = []


def proxy_label() -> str:
    """Через что ходим, без логина и пароля.

    Нужно, чтобы отличить «прокси плохой» от «прокси не подключился». При
    опечатке в .env бот молча пойдёт напрямую, получит ту же капчу, и
    причину будет не отличить.
    """
    if not AVITO_PROXY:
        return "напрямую, прокси не задан"
    scheme, _, rest = AVITO_PROXY.partition("://")
    if not rest:                                  # схему не написали
        scheme, rest = "?", AVITO_PROXY
    return f"{scheme}://{rest.rsplit('@', 1)[-1]}"  # хвост после @ - без пароля


def fetch_label() -> str:
    """Чем именно добывали страницу. Без этого «не пришло» ничего не объясняет."""
    if USE_BROWSER:
        return avito_browser.label()
    return "Chrome через curl_cffi" if USE_CFFI else "httpx"


def _proxies() -> dict | None:
    return {"http": AVITO_PROXY, "https": AVITO_PROXY} if AVITO_PROXY else None


async def _fetch_once(url: str) -> tuple[int, str]:
    """Один заход на страницу. Возвращает код ответа и текст."""
    global _cffi_session
    if USE_BROWSER:
        return await avito_browser.fetch(url)
    if USE_CFFI:
        if _cffi_session is None:
            _cffi_session = CffiSession(impersonate=IMPERSONATE, timeout=30,
                                        proxies=_proxies())
        resp = await _cffi_session.get(url)
        return resp.status_code, resp.text

    async with httpx.AsyncClient(
        headers=_headers(), timeout=30, follow_redirects=True,
        proxy=AVITO_PROXY, cookies=COOKIES,
    ) as client:
        resp = await client.get(url)
    return resp.status_code, resp.text


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


def build_search_url(query: str, category: str | None = None) -> str:
    # s=104 - сортировка "сначала новые", это ключевое: свежие лоты живут минуты.
    #
    # Вилка цен уходит прямо в адрес: это не только отбор, но и экономия -
    # чем уже коридор, тем меньше чужого приезжает вместе со страницей.
    low, high = price_band(category if category is not None else category_of(query))
    return (
        f"https://www.avito.ru/{AVITO_REGION}"
        f"?q={quote(query)}&pmax={high}&pmin={low}"
        f"&s=104&localPriority=1"
    )


def is_home_page(html: str) -> bool:
    """Подсунули ли нам главную страницу вместо выдачи поиска.

    Отличается по заголовку: у выдачи он всегда про товар и город -
    «Перфораторы купить в Краснодаре», у главной обезличенный. Судить по
    отсутствию карточек нельзя: пустая выдача выглядит так же, а это
    законный ответ, который бот обязан принимать спокойно.
    """
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.I)
    if not match:
        return False
    title = match.group(1).strip().lower()
    return "объявления на сайте авито" in title and 'data-marker="item"' not in html


async def fetch_html(url: str) -> str:
    """Забирает страницу Авито, переживая капчу Qrator.

    Капча с первого захода - обычное дело, а не приговор: вместе с ней
    приходит метка, и следующая попытка с этой меткой обычно проходит.
    Поэтому упираться в капчу сразу не надо, надо зайти ещё раз.
    """
    last = ""
    LAST_TRACE.clear()
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        status, html = await _fetch_once(url)
        LAST_TRACE.append(f"попытка {attempt}: HTTP {status}, {len(html)} симв.")

        low = html.lower()
        # Заслон Qrator приходит с кодом 429, но это не "частим запросами":
        # ни паузы, ни смена заголовков тут не помогают, помогает метка.
        if ("доступ ограничен" in low or "firewall" in low
                or "подтвердите, что вы не робот" in low):
            reason = "капча Qrator"
        elif is_home_page(html):
            # Вежливый заслон: вместо выдачи подсовывается главная. Код 200,
            # почти полмегабайта разметки, ни одного объявления. Для программы
            # это неотличимо от "по запросу ничего не нашлось", и если такое
            # проглотить, бот решит, что рынок пуст, и будет молчать неделями.
            reason = "главная вместо выдачи"
        else:
            reason = ""

        if reason:
            last = f"{reason}, попыток {attempt}"
            logger.debug(f"Монитор Авито: {reason} на попытке {attempt}, повторяю")
            if attempt < RETRY_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY * attempt)
                continue
            raise AvitoBlocked(last)

        # Код ответа - решающий довод только при запросах по http. У браузера
        # он вранью не помеха: замер 12.08.2026 - страница живого объявления
        # («iPhone 17 Pro, 256 ГБ, eSIM», цена та же, что в выдаче) пришла с
        # кодом 439. Отвергай мы её по коду, проверка «продано ли» ломалась бы
        # на каждом объявлении, и бот никогда не узнал бы реальных цен сделок.
        #
        # Настоящий заслон при этом никуда не денется: капчу и подмену
        # главной ловят проверки выше, по содержимому, а не по коду.
        if not USE_BROWSER:
            if status in (403, 429):
                raise AvitoBlocked(f"HTTP {status} - слишком часто, нужны паузы длиннее")
            if status >= 400:
                raise AvitoBlocked(f"HTTP {status} от Авито")
        return html

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


def item_url(href: str) -> str:
    """Полная ссылка на объявление, без хвоста отслеживания.

    Авито прикладывает к ссылке в выдаче метку вида ?context=H4sIAAAA... на
    полторы сотни знаков. Для перехода она не нужна - объявление открывается
    и без неё, - а сообщение с такой ссылкой не прочитать глазами.
    """
    href = href.split("?")[0]
    return href if href.startswith("http") else f"https://www.avito.ru{href}"


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
            "url": item_url(url_path),
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
            "url": item_url(href),
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


# ========== ОЧЕРЕДЬ ПОИСКОВЫХ СЛОВ ==========
def all_queries() -> list:
    """Все слова одним списком, парами «категория, слово»."""
    return [(key, q) for key, cat in CATEGORIES.items() for q in cat["queries"]]


# Докуда дошли по кольцу. Переживает круги, но не перезапуск бота - и это
# не беда: после перезапуска обход просто начнётся сначала.
_query_cursor = 0


def take_queries() -> list:
    """Отдаёт горсть слов на текущий круг и двигает указатель по кольцу.

    Гнать все девятнадцать слов подряд - вернейший способ получить капчу:
    именно так мы её и заработали. Поэтому за круг берётся несколько штук,
    а список крутится: за пять-шесть кругов слова обойдутся все, просто
    вразвалку. Ничего не теряется, растягивается только время.
    """
    global _query_cursor
    queries = all_queries()
    if QUERIES_PER_CYCLE <= 0 or QUERIES_PER_CYCLE >= len(queries):
        return queries

    start = _query_cursor % len(queries)
    _query_cursor = (start + QUERIES_PER_CYCLE) % len(queries)
    # Кольцо: если горсть не помещается в хвост, добираем из начала списка.
    doubled = queries + queries
    return doubled[start:start + QUERIES_PER_CYCLE]


def category_of(query: str) -> str | None:
    """Из какой категории это поисковое слово. Нужно, чтобы подобрать вилку цен."""
    for key, cat in CATEGORIES.items():
        if query in cat["queries"]:
            return key
    return None


def price_band(category: str | None = None) -> tuple[int, int]:
    """Вилка цен категории, а если своей нет - общая.

    Одной вилки на всё не хватает: инструмент ищется в 300-5000 ₽, айфон -
    в 40000-95000 ₽. С общим потолком айфоны просто не находились бы, а с
    айфоновым в инструмент полез бы весь строительный рынок.
    """
    cat = CATEGORIES.get(category or "", {})
    return int(cat.get("min_price", MIN_PRICE)), int(cat.get("max_price", MAX_PRICE))


def is_junk(title: str) -> bool:
    low = title.lower()
    return any(word in low for word in STOP_WORDS)


def _normalize(text: str) -> str:
    """Приводит строку к единому виду: строчные буквы, «ё» как «е», вместо
    любых знаков препинания - пробел.

    Нужно, чтобы «Сплит-система», «сплит система» и «СПЛИТ/СИСТЕМА» после
    обработки выглядели одинаково. Дефис на Авито ставят как попало, и без
    этого половина нормальных объявлений не совпала бы с запросом.
    """
    low = text.lower().replace("ё", "е")
    cleaned = re.sub(r"[^0-9a-zа-я]+", " ", low).strip()
    # Одну и ту же вещь на Авито пишут и латиницей, и кириллицей: «iPhone 17
    # Pro» и «Айфон 17 Про» - это одно объявление по смыслу и два разных
    # набора букв для программы. Без приведения к общему виду запрос
    # «iphone 17 pro» выбросил бы половину живой выдачи как чужой товар.
    return " ".join(SYNONYMS.get(word, word) for word in cleaned.split())


def _stem(word: str) -> str:
    """Грубая основа слова: отрезаем два последних знака - окончание.

    Настоящая морфология здесь ни к чему и стоила бы отдельной библиотеки.
    «Кондиционер», «кондиционера», «кондиционеры» дают одну основу
    «кондиционе», и она находится внутри любой из этих форм. Короче
    четырёх знаков не режем: от «ушм» после обрезки не осталось бы ничего,
    а совпадать с чем попало такой огрызок начал бы моментально.
    """
    return word[:max(4, len(word) - 2)]


def query_stems(query: str) -> list[str]:
    """Значимые слова запроса, приведённые к основам."""
    return [_stem(word) for word in _normalize(query).split()
            if len(word) >= MIN_WORD_LEN]


def matches_query(title: str, query: str) -> bool:
    """Про то ли объявление, что мы искали.

    Авито ищет нестрого и на «сплит система» подмешивает соседей: были и
    «Пластиковые окна двери от производителя» за 1250 ₽, и услуги мастеров.
    Все они проходили фильтр мусорных слов, ложились в базу с этим самым
    запросом и утягивали медиану вниз - после чего настоящие дешёвые
    находки переставали выглядеть дешёвыми.

    Достаточно **одного** совпавшего слова запроса, а не всех сразу.
    Требовать все - значит выбросить нормальный товар: по «шуруповерт
    аккумуляторный» половина объявлений называется просто «Шуруповерт
    Makita 18V», по «кондиционер бу» - «Кондиционер Ballu». Одного слова
    хватает: мусор из чужих категорий не содержит ни одного.
    """
    stems = query_stems(query)
    if not stems:            # запрос из одних коротких слов - фильтровать нечем
        return True
    low = _normalize(title)
    return any(stem in low for stem in stems)


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
    выглядеть дешёвыми. По той же причине не попадает и чужой товар,
    который Авито подмешивает в выдачу по нестрогому совпадению.
    """
    fresh = []
    now = time.time()
    with _connect() as conn:
        for item in items:
            if not item["item_id"] or not item["price"]:
                continue
            if is_junk(item["title"]):
                continue
            if not matches_query(item["title"], query):
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


def rate_deal(item: dict, query: str, category: str | None = None) -> tuple[bool, str]:
    """Решает, стоит ли будить владельца из-за этого лота."""
    price = item["price"]
    low, high = price_band(category if category is not None else category_of(query))
    if price > high or price < low:
        return False, ""
    if is_junk(item["title"]):
        return False, ""
    # Вторая застава: в базу такое уже не попадает, но будить владельца
    # чужим товаром нельзя и при прямом вызове - например из бота.
    if not matches_query(item["title"], query):
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
    except avito_browser.BrowserUnavailable as exc:
        return (f"❌ Браузер не поднялся: {exc}\n\n"
                f"Без браузера Авито не читается совсем — проверено.")
    except AvitoBlocked as exc:
        hint = ("Авито пропускает только настоящий видимый браузер. Проверь, "
                "что окно браузера открылось: если оно закрыто или бот запущен "
                "службой, будет ровно это." if USE_BROWSER else
                "Сейчас ходим по http (AVITO_FETCH=http), а так Авито не пускает. "
                "Убери эту настройку, чтобы вернуться к браузеру.")
        return (f"🚫 Авито блокирует запросы: {exc}\n"
                f"ходили: {fetch_label()}\n\n{hint}")
    except Exception as exc:
        return f"❌ Не достучались до Авито: {type(exc).__name__}: {exc}"

    via_json = parse_from_json(html)
    via_dom = parse_from_dom(html)
    items = via_json or via_dom

    out = [
        f"🔍 Тест по запросу «{query}»",
        f"чем ходили: {fetch_label()}",
        f"через что: {proxy_label()}",
        f"страница получена: {len(html):,} символов".replace(",", " "),
        f"через JSON: {len(via_json)} карточек",
        f"через разметку: {len(via_dom)} карточек" + ("" if BS4_AVAILABLE else " (bs4 не установлен)"),
        "",
    ]
    if not items:
        out.append("⚠️ Ноль карточек. Авито сменил вёрстку — нужно поправить парсер.")
        return "\n".join(out)

    # Отдельно показываем отсеянное. Авито ищет нестрого и подмешивает
    # чужой товар и услуги мастеров; без этого списка не видно, режет ли
    # фильтр лишнее — а режет он то, по чему потом считается медиана.
    good, dropped = [], []
    for item in items:
        if is_junk(item["title"]) or not matches_query(item["title"], query):
            dropped.append(item)
        else:
            good.append(item)

    out.append(f"подходит запросу: {len(good)} из {len(items)}")
    out.append("")
    out.append("Первые находки:")
    # Со ссылкой, а не одним названием. Иначе проверка отвечает на вопрос
    # «видит ли бот выдачу», но не на вопрос «правда ли там эта цена», -
    # а второй для перекупа и есть главный.
    for item in good[:5]:
        out.append(f"• {item['title'][:55]} — {item['price']:,} ₽".replace(",", " "))
        out.append(f"  {item['url']}")
    if dropped:
        out.append("")
        out.append("Отсеяно как не то:")
        for item in dropped[:5]:
            out.append(f"  ✗ {item['title'][:55]} — {item['price']:,} ₽".replace(",", " "))
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
            for cat_key, query in take_queries():
                try:
                    html = await fetch_html(build_search_url(query, cat_key))
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
                    good, note = rate_deal(item, query, cat_key)
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
            # Браузер живёт всё время работы монитора, и сам он не закроется:
            # останется висеть чужим процессом до перезагрузки компьютера.
            await avito_browser.close()
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

    queries = len(all_queries())
    per_cycle = len(take_queries())
    cycle_min = max(1, int((per_cycle * (REQ_DELAY_MIN + REQ_DELAY_MAX) / 2 + CYCLE_PAUSE) / 60))
    # Сколько ждать, пока очередь обойдёт все слова и вернётся к первому.
    sweep_min = cycle_min * -(-queries // per_cycle)

    # Вилка у каждой категории своя, одним числом её уже не показать:
    # инструмент ищется в 300-5000 ₽, айфоны - в 40000-95000 ₽.
    bands = []
    for key, cat in CATEGORIES.items():
        low, high = price_band(key)
        bands.append(f"{cat['emoji']} {cat['name']}: "
                     f"{low:,}–{high:,} ₽".replace(",", " "))

    await update.message.reply_text(
        f"{'🟢 Монитор работает' if is_enabled() else '⚪️ Монитор выключен'}\n\n"
        f"Регион: {AVITO_REGION}\n\n"
        + "\n".join(bands) + "\n\n"
        f"Категорий: {len(CATEGORIES)}, запросов: {queries}\n"
        f"За круг: {per_cycle} слов, ~{cycle_min} мин\n"
        f"Полный обход всех слов: ~{sweep_min} мин\n\n"
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
