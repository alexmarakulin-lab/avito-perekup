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
import subprocess
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
import wb_source

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
#
# Уменьшена с 15 минут до 10 вместе с расширением списка слов до сорока: с
# прежними настройками полный обход занимал бы четыре часа, а «сразу
# уведомление» при таком круге - пустой звук. Выходит около двадцати
# обращений в час против сорока, которыми блокировка была заработана, - и
# те сорок шли библиотекой, а не настоящим браузером, к которому у защиты
# вопросов заметно меньше.
CYCLE_PAUSE = int(os.getenv("AVITO_CYCLE_PAUSE", "600"))

# Сколько поисковых слов обходить за один круг. Ноль - все подряд, как было.
#
# Слов девятнадцать, и гнать их пачкой - вернейший способ снова попасть в
# капчу. Поэтому за круг берётся горсть, а список крутится по кольцу: за
# несколько кругов слова всё равно обойдутся все, просто вразвалку. Заодно
# это прямо экономит деньги, если Авито читается через прокси с оплатой за
# трафик: каждая страница выдачи весит без малого мегабайт.
QUERIES_PER_CYCLE = int(os.getenv("AVITO_QUERIES_PER_CYCLE", "5"))

# Цена считается вкусной, если она не выше этой доли от медианы рынка.
#
# 0.5 - то есть вдвое дешевле рынка. Порог поднят 27.08.2026 с 0.6 после
# замера на живом потоке: на прежнем «дешевле на 20%» приходило 34
# сообщения за четверть часа, и настоящие находки в этом потоке тонули.
# Смысл бота не в том, чтобы показать весь рынок, а в том, чтобы разбудить
# ради лота, за которым стоит ехать.
DEAL_RATIO = float(os.getenv("AVITO_DEAL_RATIO", "0.5"))
# Медиане можно верить начиная с этого количества наблюдений.
MIN_STATS_SAMPLE = 8
# Глубина истории для расчёта медианы, дней.
STATS_WINDOW_DAYS = 21

# ========== КАНАЛ ==========
# Куда, кроме личной переписки, выкладывать находки. Пусто - канала нет и
# ничего никуда не уходит; так по умолчанию.
#
# Годится и номер вида -1001234567890, и публичное имя вида @krd_nahodki.
CHANNEL_CHAT = os.getenv("AVITO_CHANNEL_CHAT", "").strip()

# Насколько канал отстаёт от владельца, секунд. Полчаса.
#
# Задержка - не украшение, а весь смысл затеи. Владелец перекупает: канал
# с его же находками без задержки означает, что подписчики забирают лоты
# раньше него самого. Полчаса хватает, чтобы позвонить и договориться, и
# при этом канал остаётся живым - объявления в Краснодаре столько висят.
# Отсюда же вырастает платный «ранний доступ», если он однажды понадобится:
# это будет та же очередь, только с разным сроком для разных читателей.
CHANNEL_DELAY = int(os.getenv("AVITO_CHANNEL_DELAY", "1800"))

# Порог канала - свой, мягче владельцева.
#
# Владельцу шлётся только то, ради чего стоит ехать (вдвое дешевле рынка),
# а такого набирается единицы в неделю - канал бы стоял пустым. Читателю
# же интересен любой стоящий лот: он не едет за маржой, он покупает себе.
CHANNEL_RATIO = float(os.getenv("AVITO_CHANNEL_RATIO", "0.7"))

# Дни недели для постов в канал: 0 - понедельник, 6 - воскресенье.
# Пусто - пост выключен.
#
# Разнесены по разным дням не для красоты. Канал, в котором пусто шесть
# дней и густо в седьмой, читатель отписывает: лента выглядит заброшенной
# ровно тогда, когда он на неё смотрит. Два поста в разные дни держат его
# живым даже в неделю без единой находки.
CHANNEL_DIGEST_DAY = os.getenv("AVITO_CHANNEL_DIGEST_DAY", "6").strip()
CHANNEL_PROOF_DAY = os.getenv("AVITO_CHANNEL_PROOF_DAY", "2").strip()
CHANNEL_POST_HOUR = int(os.getenv("AVITO_CHANNEL_POST_HOUR", "19"))

# Пауза между сообщениями в канал, секунд. Telegram считает частоту по
# каналу отдельно и на залпе отвечает отказом.
CHANNEL_SEND_PAUSE = 1

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
            "компрессор воздушный",
            "генератор бензиновый",
            "тепловая пушка",
            "плиткорез",
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
            "газонокосилка",
            "мойка высокого давления",
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
            "пылесос",
            "посудомоечная машина",
            "духовой шкаф",
        ],
    },
    # Электроника - самый ходовой перекуп после инструмента, и вилка у неё
    # своя: ноутбук за 5000 ₽ не бывает, а за 200000 неинтересен.
    "electronics": {
        "name": "Электроника",
        "emoji": "💻",
        "min_price": 3000,
        "max_price": 60000,
        "queries": [
            "ноутбук",
            "телевизор",
            "монитор",
            "playstation",
            "ipad",
            "airpods",
            "фотоаппарат",
        ],
    },
    "transport": {
        "name": "Вело и самокаты",
        "emoji": "🚲",
        "min_price": 3000,
        "max_price": 50000,
        "queries": [
            "велосипед",
            "электросамокат",
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
    # ===== Wildberries =====
    # Вторая площадка. Ozon и Яндекс Маркет проверены настоящим браузером и
    # не читаются: 403 и свои проверки. Подробности и замеры - в wb_source.py.
    "wb_apple": {
        "name": "WB · Apple",
        "emoji": "🍏",
        "source": "wb",
        "min_price": 5000,
        "max_price": 150000,
        "queries": [
            "iphone",
            "ipad",
            "macbook",
            "apple watch",
            "airpods",
        ],
    },
    "wb_shoes": {
        "name": "WB · Обувь",
        "emoji": "👟",
        "source": "wb",
        "min_price": 3000,
        "max_price": 30000,
        "queries": [
            "кроссовки nike",
            "кроссовки adidas",
            "кроссовки new balance",
        ],
    },
    "wb_clothes": {
        "name": "WB · Одежда",
        "emoji": "🧥",
        "source": "wb",
        "min_price": 3000,
        "max_price": 40000,
        "queries": [
            "куртка carhartt",
            "худи stone island",
            "джинсы levis",
            "ветровка the north face",
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

            -- Очередь в канал. Лежит в базе, а не в памяти, потому что
            -- полчаса ожидания запросто переживают перезапуск бота: дома
            -- он поднимается заново после каждого обрыва связи, и очередь
            -- в памяти означала бы, что находка теряется молча.
            CREATE TABLE IF NOT EXISTS channel_queue (
                item_id   TEXT PRIMARY KEY,
                category  TEXT NOT NULL,
                note      TEXT,
                market    INTEGER,
                due_at    REAL NOT NULL,
                posted_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_queue_due ON channel_queue(posted_at, due_at);
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


def get_channel_chat() -> str | int | None:
    """Куда выкладывать находки, кроме личной переписки.

    Публичное имя канала возвращается строкой, номер - числом: Telegram
    принимает и то, и другое, а вот строку «-1001234567890» он принимает
    не везде, поэтому число приводится честно.
    """
    raw = (get_setting("channel_chat") or CHANNEL_CHAT or "").strip()
    if not raw:
        return None
    if raw.startswith("@"):
        return raw
    try:
        return int(raw)
    except ValueError:
        return raw


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


async def _fetch_once(url: str, source: str = "avito") -> tuple[int, str]:
    """Один заход на страницу. Возвращает код ответа и текст."""
    global _cffi_session
    if USE_BROWSER:
        if source == "wb":
            # Своя примета готовности и без прогрева: заход на главную нужен
            # защите Авито, для Wildberries это лишний запрос.
            return await avito_browser.fetch(url, wait_for=wb_source.CARDS_SELECTOR,
                                             warm=False)
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
    cat = category if category is not None else category_of(query)
    if source_of(cat) == "wb":
        return wb_source.build_search_url(query)

    # Вилка цен уходит прямо в адрес: это не только отбор, но и экономия -
    # чем уже коридор, тем меньше чужого приезжает вместе со страницей.
    low, high = price_band(cat)
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


async def fetch_html(url: str, source: str = "avito") -> str:
    """Забирает страницу Авито, переживая капчу Qrator.

    Капча с первого захода - обычное дело, а не приговор: вместе с ней
    приходит метка, и следующая попытка с этой меткой обычно проходит.
    Поэтому упираться в капчу сразу не надо, надо зайти ещё раз.
    """
    last = ""
    LAST_TRACE.clear()
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        status, html = await _fetch_once(url, source)
        LAST_TRACE.append(f"попытка {attempt}: HTTP {status}, {len(html)} симв.")

        if source == "wb":
            # У Wildberries свои приметы заслона, а проверка на подмену
            # главной - чисто авитовская, и здесь только мешала бы.
            if wb_source.is_blocked(html):
                last = f"Wildberries закрылся, попыток {attempt}"
                if attempt < RETRY_ATTEMPTS:
                    await asyncio.sleep(RETRY_DELAY * attempt)
                    continue
                raise AvitoBlocked(last)
            return html

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


# Возраст объявления Авито пишет словами: «2 минуты назад», «Вчера»,
# «3 дня назад». Точного времени нет нигде - ни в атрибутах, ни в JSON,
# проверено 13.08.2026. Слов достаточно: нам важно отличить «только что»
# от «неделю висит», а не секунды.
AGE_UNITS = [
    ("секунд", 0),
    ("минут", 1),
    ("час", 60),
    ("дня", 1440), ("дней", 1440), ("день", 1440),
    ("недел", 10080),
    ("месяц", 43200),
]


def parse_age(text: str) -> int | None:
    """Сколько минут назад выложено. None - не разобрали.

    Свежесть для перекупа решает всё: лот дешевле рынка живёт минуты, и
    разница между «выложено 5 минут назад» и «висит третий день» - это
    разница между находкой и тем, что уже сто раз посмотрели и не взяли.
    """
    if not text:
        return None
    low = text.lower().replace("ё", "е")
    if "вчера" in low:
        return 1440
    if "сегодня" in low:
        return 0
    number = re.search(r"\d+", low)
    for word, minutes in AGE_UNITS:
        if word in low:
            return int(number.group()) * minutes if number else minutes
    return None


def human_age(minutes: int | None) -> str:
    """Возраст словами, для сообщения."""
    if minutes is None:
        return ""
    if minutes < 60:
        return f"{minutes} мин назад"
    if minutes < 1440:
        return f"{minutes // 60} ч назад"
    days = minutes // 1440
    return "вчера" if days == 1 else f"{days} дн назад"


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
        # Район лежит под item-location. Прежний отбор искал item-address и
        # geo-address - таких имён на карточке нет вовсе, и адрес всё это
        # время приходил пустым. Молча: пустая строка ошибкой не выглядит.
        addr_el = block.select_one('[data-marker="item-location"]')
        date_el = block.select_one('[data-marker="item-date"]')
        score_el = block.select_one('[data-marker="seller-rating/score"]')
        reviews_el = block.select_one('[data-marker="seller-info/summary"]')

        items.append({
            "item_id": str(item_id),
            "title": title_el.get_text(strip=True),
            "price": price,
            "url": item_url(href),
            "address": addr_el.get_text(" ", strip=True) if addr_el else "",
            # Дальше - то, что нужно для оценки лота, а не для его поиска.
            "age_min": parse_age(date_el.get_text(" ", strip=True) if date_el else ""),
            "seller_score": score_el.get_text(strip=True) if score_el else "",
            "seller_reviews": reviews_el.get_text(" ", strip=True) if reviews_el else "",
        })
    return items


def parse_search(html: str, source: str = "avito") -> list:
    if source == "wb":
        return wb_source.parse(html)
    return _parse_avito(html)


def _parse_avito(html: str) -> list:
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

    Гнать весь список подряд - вернейший способ получить капчу: именно так
    мы её и заработали. Поэтому за круг берётся несколько штук, а список
    крутится: за несколько кругов слова обойдутся все, просто вразвалку.
    Ничего не теряется, растягивается только время.

    Времени этого больше, чем кажется: слов теперь пятьдесят одно (39 на
    Авито, 12 на Wildberries), и при горсти в пять штук полный обход - это
    одиннадцать кругов, около трёх часов. Цифру стоит держать в уме,
    добавляя новые слова: каждое из них растягивает круг для всех
    остальных.
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


def source_of(category: str | None) -> str:
    """С какой площадки категория. Умолчание - Авито, как было всегда."""
    return CATEGORIES.get(category or "", {}).get("source", "avito")


def stats_key(source: str, query: str) -> str:
    """Под каким именем копить цены.

    Площадка входит в имя, и это не украшение. «iphone» на Авито - это б/у
    с рук, на Wildberries - восстановленный из магазина. Свалив их в одну
    кучу, мы получили бы медиану, которой нет на рынке, и оба ряда находок
    оценивались бы неверно.
    """
    return query if source == "avito" else f"{source}:{query}"


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


def page_median(items: list) -> int | None:
    """Медиана цен по одной странице выдачи.

    Это способ судить о выгоде **сразу**, не дожидаясь недель накопления.
    Страница выдачи - это полсотни объявлений про один и тот же товар,
    выложенных прямо сейчас: готовый срез рынка на сегодня. Если среди
    полусотни перфораторов по 3000 ₽ появился один за 1200 - это видно в ту
    же секунду, и никакая история для этого не нужна.

    Раньше без истории бот слал вообще всё, что укладывалось в бюджет,
    «чтобы копилась статистика». При четырёх словах это было терпимо; при
    сорока превратилось бы в сотни сообщений в день, и настоящие находки
    утонули бы среди них.
    """
    prices = sorted(i["price"] for i in items if i.get("price", 0) > 0)
    if len(prices) < MIN_STATS_SAMPLE:
        return None
    return int(statistics.median(prices))


def market_reference(key: str, page_ref: int | None = None) -> int | None:
    """С чем сравниваем цену: накопленная медиана, а нет её - сегодняшняя выдача."""
    median, _ = median_price(key)
    return median if median is not None else page_ref


def rate_deal(item: dict, query: str, category: str | None = None,
              page_ref: int | None = None,
              key: str | None = None,
              ratio: float | None = None) -> tuple[bool, str]:
    """Решает, стоит ли будить владельца из-за этого лота.

    page_ref - медиана текущей страницы выдачи. Нужна, пока своей истории
    по слову ещё нет: без неё первые недели бот либо молчал бы совсем, либо
    слал всё подряд.

    ratio - порог, ниже которого цена считается вкусной. Он вынесен в
    руки зовущего, потому что порогов теперь два: владельцу шлётся только
    то, ради чего стоит ехать, каналу - всё стоящее. Одна и та же карточка
    оценивается дважды, разной меркой.
    """
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

    # Цифры сюда не пишем: разницу с рынком показывает само уведомление,
    # одной строкой. Написанная дважды, она вдобавок расходилась в
    # округлении - одна и та же скидка выходила и −79%, и −78%, и выглядело
    # это как ошибка расчёта.
    # Имя, под которым копятся цены. У Wildberries оно своё: «iphone» с рук
    # и «iphone» из магазина - разные рынки, и общая медиана врала бы обоим.
    threshold = DEAL_RATIO if ratio is None else ratio

    median, sample = median_price(key or query)
    if median is not None:
        # Второй, мягкий порог («ниже медианы на 20%») убран 27.08.2026: он
        # и давал основной поток. Лот, который дешевле рынка на четверть,
        # интересен глазу, но не стоит поездки - а разбудив на нём, бот
        # обесценивает и то сообщение, ради которого стоило ехать. Такие
        # лоты никуда не деваются: они ложатся в базу и видны в отчёте.
        if price / median > threshold:
            return False, ""
        # Огонёк означает одно и то же везде - «вдвое дешевле рынка». В
        # канале порог мягче, и если бы 🔥 стоял на каждом сообщении, читатель
        # за неделю перестал бы его замечать - а вместе с ним и настоящие
        # находки, ради которых всё и затевалось.
        if price / median <= DEAL_RATIO:
            return True, f"🔥 заметно дешевле медианы за {STATS_WINDOW_DAYS} дн ({sample} набл.)"
        return True, f"дешевле медианы за {STATS_WINDOW_DAYS} дн ({sample} набл.)"

    if page_ref:
        # Спрос тот же, что и к накопленной истории, и снисхождения тут быть
        # не может: полсотни объявлений - срез грубый, там вперемешку разные
        # модели и состояния. Что не дотянуло - ляжет в историю и завтра
        # поучаствует в расчёте.
        if price / page_ref > threshold:
            return False, ""
        if price / page_ref <= DEAL_RATIO:
            return True, "🔥 заметно дешевле сегодняшней выдачи (истории пока нет)"
        return True, "дешевле сегодняшней выдачи (истории пока нет)"

    # Ни истории, ни страницы - судить не по чему. Молчим: лот всё равно
    # уже в базе и завтра поучаствует в расчёте.
    return False, ""


# ========== ОТПРАВКА ==========
def money(n) -> str:
    """Число с пробелами по разрядам: 7500 -> «7 500».

    Отдельной функцией, а не `.replace(",", " ")` по готовой строке, - и
    это не придирка. Замена по всей строке съедает не только разряды, но
    и обычные запятые: у названий с Авито («Перфоратор Bosch, отличное
    состояние») она выгрызала запятую прямо из середины слова.
    """
    return f"{n:,}".replace(",", " ")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение по числу: 1 слово, 2 слова, 5 слов, 51 слово.

    Правило смотрит на две последние цифры, а не на одну: у чисел от 11 до
    14 окончание не такое, как у 21 и 31, хотя последняя цифра та же.
    """
    n = abs(n)
    if n % 100 in range(11, 15):
        return many
    last = n % 10
    if last == 1:
        return one
    if last in (2, 3, 4):
        return few
    return many


def build_alert(item: dict, category: str, note: str,
                market: int | None = None) -> str:
    """Собирает подробное уведомление о находке.

    Три строки «название, цена, ссылка» решения не дают: чтобы понять,
    ехать или нет, нужно видеть разницу с рынком, свежесть и с кем имеешь
    дело. Всё это на карточке есть, и не показывать его - значит заставлять
    открывать объявление ради того, что уже известно.
    """
    cat = CATEGORIES.get(category, {})

    lines = [f"{cat.get('emoji', '📦')} <b>{cat.get('name', category)}</b>",
             "", item["title"], ""]

    if market:
        diff = market - item["price"]
        share = int(round(diff / market * 100))
        lines.append(f"💰 <b>{money(item['price'])} ₽</b>  "
                     f"<s>{money(market)} ₽</s>  −{share}%")
        # Разница с рынком - это потолок навара, а не навар: доставка, торг
        # при перепродаже и время съедят часть. Так и подписано.
        lines.append(f"📈 разница с рынком: <b>{money(diff)} ₽</b> до вычета хлопот")
    else:
        lines.append(f"💰 <b>{money(item['price'])} ₽</b>")

    age = item.get("age_min")
    if age is not None:
        # Свежее объявление - это и есть весь смысл затеи: дешёвый лот
        # живёт минуты. Отмечаем отдельно, чтобы было видно с первого взгляда.
        mark = "🕐"
        if age <= 30:
            mark = "⚡️ <b>только что</b>,"
        elif age >= 1440:
            mark = "🐌"
        lines.append(f"{mark} {human_age(age)}")

    if item.get("address"):
        lines.append(f"📍 {item['address']}")

    score, reviews = item.get("seller_score", ""), item.get("seller_reviews", "")
    seller = " · ".join(x for x in (f"{score} ★" if score else "", reviews) if x)
    if seller:
        lines.append(f"👤 {seller}")

    if note:
        lines.append("")
        lines.append(note)

    lines.append("")
    lines.append(item["url"])
    return "\n".join(lines)


async def send_alert(bot, chat_id: int, item: dict, category: str, note: str,
                     market: int | None = None):
    await bot.send_message(chat_id=chat_id,
                           text=build_alert(item, category, note, market),
                           parse_mode="HTML", disable_web_page_preview=False)
    with _connect() as conn:
        conn.execute("UPDATE items SET alerted = 1 WHERE item_id = ?", (item["item_id"],))


# ========== ОЧЕРЕДЬ В КАНАЛ ==========
def queue_for_channel(item: dict, category: str, note: str,
                      market: int | None = None, delay: int | None = None):
    """Кладёт находку в очередь на выкладку в канал.

    Повторно та же карточка в очередь не встаёт: `INSERT OR IGNORE` по
    первичному ключу. Это не мелочь - одно и то же объявление попадает в
    выдачу круг за кругом, пока висит.
    """
    wait = CHANNEL_DELAY if delay is None else delay
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO channel_queue (item_id, category, note, market, due_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (item["item_id"], category, note, market, time.time() + wait),
        )


def due_for_channel(limit: int = 5) -> list:
    """Что уже отстояло свою очередь и готово к выкладке.

    Забирается горстью, а не всё разом: после долгого простоя - скажем,
    компьютер был выключен сутки - в очереди накопится десяток находок, и
    вывалить их одним залпом значит и получить от Telegram отказ по
    частоте, и завалить читателя стеной сообщений.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT q.item_id, q.category, q.note, q.market, "
            "       i.title, i.price, i.url, i.address "
            "FROM channel_queue q JOIN items i ON i.item_id = q.item_id "
            "WHERE q.posted_at IS NULL AND q.due_at <= ? "
            "ORDER BY q.due_at LIMIT ?", (time.time(), limit)
        ).fetchall()
    return [dict(r) for r in rows]


def mark_posted(item_id: str):
    with _connect() as conn:
        conn.execute("UPDATE channel_queue SET posted_at = ? WHERE item_id = ?",
                     (time.time(), item_id))


async def post_due_to_channel(bot) -> int:
    """Выкладывает в канал всё, что отстояло очередь. Возвращает - сколько.

    Зовётся часто и вхолостую: очередь смотрится между запросами к Авито,
    то есть примерно раз в минуту. Иначе находка ждала бы не полчаса, а
    полчаса плюс остаток круга - а круг идёт четверть часа.
    """
    chat = get_channel_chat()
    if not chat:
        return 0

    posted = 0
    for row in due_for_channel():
        item = {"item_id": row["item_id"], "title": row["title"], "price": row["price"],
                "url": row["url"], "address": row["address"]}
        try:
            await bot.send_message(
                chat_id=chat,
                text=build_alert(item, row["category"], row["note"] or "", row["market"]),
                parse_mode="HTML", disable_web_page_preview=False)
        except Exception as exc:
            # Метку не ставим: не вышло - полежит и уйдёт следующей попыткой.
            # Единственный случай, когда это плохо, - канал настроен неверно;
            # тогда в логе будет ровно эта строка, круг за кругом.
            logger.error(f"Канал: не выложил {row['item_id']} ({exc})")
            break
        mark_posted(row["item_id"])
        posted += 1
        # Секунда между сообщениями дешевле разбора отказов по частоте.
        await asyncio.sleep(CHANNEL_SEND_PAUSE)

    if posted:
        logger.info(f"Канал: выложено {posted}")
    return posted


async def cmd_channel_test(update, context):
    """Проверяет канал прямо сейчас: настроен ли и пускает ли бота.

    Без этой проверки о том, что бота забыли сделать администратором,
    узнать было неоткуда: находка молча ложилась в очередь, через полчаса
    не выкладывалась, а отказ уходил в лог. Обнаруживалось это через сутки
    по пустому каналу.
    """
    init_db()
    chat = get_channel_chat()
    if not chat:
        await update.message.reply_text(
            "📢 Канал не настроен.\n\n"
            "Запусти «Настроить бота» - он спросит канал четвёртым вопросом. "
            "Вписывается либо @имя публичного канала, либо номер закрытого "
            "(начинается на -100).")
        return

    try:
        sent = await context.bot.send_message(
            chat_id=chat,
            text="🔧 Проверка связи. Бот подключён к каналу, находки пойдут сюда.\n\n"
                 "Это сообщение можно удалить.")
    except Exception as exc:
        # Текст отказа у Telegram английский и невнятный. Разбираем его на
        # человеческий: причин на деле всего три, и все чинятся по-разному.
        reason = str(exc).lower()
        if "not enough rights" in reason or "need administrator" in reason:
            hint = ("Бот в канале есть, но публиковать не может.\n\n"
                    "Канал → Администраторы → найти бота → включить "
                    "«Публикация сообщений».")
        elif "chat not found" in reason:
            hint = ("Такого канала Telegram не знает.\n\n"
                    "Если канал публичный - проверь имя, оно пишется с собачкой. "
                    "Если закрытый - нужен номер вида -1001234567890, а не имя.")
        elif "kicked" in reason or "not a member" in reason:
            hint = ("Бота нет в канале.\n\n"
                    "Канал → Администраторы → Добавить администратора → "
                    "найти бота по имени.")
        else:
            hint = f"Telegram ответил: {exc}"
        await update.message.reply_text(f"❌ Канал не принял сообщение.\n\n{hint}")
        return

    with _connect() as conn:
        waiting = conn.execute(
            "SELECT COUNT(*) c FROM channel_queue WHERE posted_at IS NULL"
        ).fetchone()["c"]

    await update.message.reply_text(
        f"✅ Канал на связи: {chat}\n\n"
        f"Проверочное сообщение выложено, можешь его удалить.\n\n"
        f"Отставание: {CHANNEL_DELAY // 60} мин после тебя\n"
        f"Порог канала: дешевле рынка на {int((1 - CHANNEL_RATIO) * 100)}%\n"
        f"Сейчас в очереди: {waiting}")


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
                f"   новых: {len(prices)} | медиана: {money(int(statistics.median(prices)))} ₽"
                f" | мин: {money(prices[0])} ₽"
            )
            lines.append(f"   дешевле всех: {cheapest['title'][:60]}")

        sold = conn.execute(
            "SELECT title, price FROM items WHERE sold_at > ? ORDER BY sold_at DESC LIMIT 5",
            (since,),
        ).fetchall()

    if sold:
        lines.append("\n<b>Ушло с рынка</b> (реальная цена продажи):")
        for row in sold:
            lines.append(f"   • {row['title'][:50]} — {money(row['price'])} ₽")
    else:
        lines.append("\n<i>Продаж за период не зафиксировано.</i>")

    if not total:
        lines.append("\n⚠️ Ноль новых карточек за сутки — похоже, парсер не видит выдачу. "
                     "Проверь /avito_test.")
    return "\n".join(lines)


# ========== ПОСТЫ ДЛЯ КАНАЛА ==========
# Отчёт `build_report` писан для владельца: там есть служебное «парсер не
# видит выдачу» и сухие цифры по всем категориям разом. Читателю канала
# нужно другое - короткий и понятный итог, поэтому посты свои.
def build_channel_digest(days: int = 7) -> str:
    """Недельный итог по рынку. Контент даже в тихую неделю."""
    since = time.time() - days * 86400
    with _connect() as conn:
        watched = conn.execute(
            "SELECT COUNT(*) c FROM items WHERE first_seen > ?", (since,)
        ).fetchone()["c"]
        published = conn.execute(
            "SELECT COUNT(*) c FROM channel_queue WHERE posted_at > ?", (since,)
        ).fetchone()["c"]

        rows = []
        for key, cat in CATEGORIES.items():
            prices = [r["price"] for r in conn.execute(
                "SELECT price FROM items WHERE category = ? AND first_seen > ? AND price > 0",
                (key, since)).fetchall()]
            if len(prices) < 5:
                continue
            rows.append((len(prices), cat, sorted(prices)))

        # Лучшая находка недели - та, у которой разница с рынком вышла
        # больше всех. Она и есть главное доказательство, что канал нужен.
        best = conn.execute(
            "SELECT i.title, i.price, q.market FROM channel_queue q "
            "JOIN items i ON i.item_id = q.item_id "
            "WHERE q.posted_at > ? AND q.market > 0 AND i.price > 0 "
            "ORDER BY (CAST(i.price AS REAL) / q.market) LIMIT 1", (since,)
        ).fetchone()

    if not watched:
        return ""

    lines = [f"📊 <b>Неделя на рынке Краснодара</b>\n",
             f"Просмотрено объявлений: <b>{money(watched)}</b>"]
    if published:
        lines.append(f"Попало в канал: <b>{published}</b> — те, что дешевле рынка "
                     f"на {int((1 - CHANNEL_RATIO) * 100)}% и больше")
    lines.append("")

    # Только оживлённые категории, и не больше пяти: длинный список цифр
    # читатель пролистывает целиком, вместе с тем, ради чего он написан.
    for count, cat, prices in sorted(rows, reverse=True, key=lambda r: r[0])[:5]:
        lines.append(
            f"{cat['emoji']} <b>{cat['name']}</b> — новых {count}, "
            f"медиана {money(int(statistics.median(prices)))} ₽, "
            f"дешевле всех {money(prices[0])} ₽")

    if best and best["market"]:
        diff = 1 - best["price"] / best["market"]
        lines.append(f"\n<b>Находка недели</b>\n"
                     f"{best['title'][:60]} — {money(best['price'])} ₽ "
                     f"при рынке {money(best['market'])} ₽ (−{int(diff * 100)}%)")

    return "\n".join(lines)


def build_sold_proof(days: int = 7, limit: int = 5) -> str:
    """Что из выложенного уже ушло с Авито.

    Это самое сильное, что канал может сказать о себе: не «у нас дёшево»,
    а «смотрите быстро, вот это уже разобрали». Берутся только те лоты,
    что прошли через канал - про чужие находки говорить нечестно.

    Слово «продано» тут не годится: бот видит лишь то, что объявление
    снято с публикации. Обычно это и значит продажу, но не всегда, и
    обещать читателю больше, чем знаешь, - верный способ потерять доверие
    ровно один раз и насовсем.
    """
    since = time.time() - days * 86400
    with _connect() as conn:
        rows = conn.execute(
            "SELECT i.title, i.price, q.market, i.sold_at, q.posted_at "
            "FROM channel_queue q JOIN items i ON i.item_id = q.item_id "
            "WHERE q.posted_at IS NOT NULL AND i.sold_at > ? "
            "ORDER BY i.sold_at DESC LIMIT ?", (since, limit)
        ).fetchall()

    if not rows:
        return ""

    lines = ["🏃 <b>Разобрали за неделю</b>\n",
             "Из того, что выкладывали здесь, уже снято с Авито:\n"]
    for r in rows:
        line = f"• {r['title'][:55]} — {money(r['price'])} ₽"
        if r["market"]:
            line += f" (рынок {money(r['market'])} ₽)"
        hours = (r["sold_at"] - r["posted_at"]) / 3600 if r["posted_at"] else None
        if hours is not None and hours >= 0:
            line += f", ушло за {int(hours)} ч" if hours >= 1 else ", ушло за час"
        lines.append(line)

    lines.append("\nВыводы делайте сами: хорошее здесь живёт часами, "
                 "а не днями.")
    return "\n".join(lines)


async def post_weekly(bot) -> str | None:
    """Раз в неделю выкладывает в канал сводку и разбор проданного.

    Возвращает имя выложенного поста или None. Отметка о выкладке лежит в
    базе, а не в памяти: дома бот перезапускается по нескольку раз в день,
    и памятью «уже постили» он бы не удержал - канал получал бы одну и ту
    же сводку после каждого подъёма.
    """
    chat = get_channel_chat()
    if not chat:
        return None

    now = datetime.now()
    if now.hour < CHANNEL_POST_HOUR:
        return None
    today = now.date().isoformat()
    weekday = str(now.weekday())

    for name, day, builder in (("digest", CHANNEL_DIGEST_DAY, build_channel_digest),
                               ("proof", CHANNEL_PROOF_DAY, build_sold_proof)):
        if not day or weekday != day:
            continue
        if get_setting(f"channel_{name}_date") == today:
            continue

        text = builder()
        # Пустой пост не выкладывается, но день всё равно отмечается
        # закрытым: иначе бот весь вечер, каждый круг, заново ходил бы за
        # тем же пустым результатом.
        if text:
            try:
                await bot.send_message(chat_id=chat, text=text, parse_mode="HTML")
            except Exception as exc:
                logger.error(f"Канал: недельный пост «{name}» не ушёл ({exc})")
                return None
            logger.info(f"Канал: выложен недельный пост «{name}»")
        set_setting(f"channel_{name}_date", today)
        return name if text else None

    return None


def parse_powercfg(text: str) -> int | None:
    """Достаёт из ответа powercfg время до сна от сети, в секундах.

    Разбор нарочно не опирается на английские слова: на русской Windows
    powercfg отвечает по-русски («Индекс текущего параметра питания от
    сети переменного тока»), и проверка, написанная под английский текст,
    молча решала бы, что ответ непонятен.

    Опора на порядок чисел надёжнее. Ответ устроен всегда одинаково:
    сначала границы и шаг возможных значений, потом два индекса - от сети
    и от батареи. Значит нужное число - предпоследнее шестнадцатеричное
    в ответе, каким бы языком оно ни было подписано.
    """
    numbers = re.findall(r"0x([0-9a-fA-F]+)", text)
    if len(numbers) < 2:
        return None
    return int(numbers[-2], 16)


def sleep_setting() -> tuple:
    """Спит ли компьютер сам по себе. Только Windows, только от сети.

    Проверка выглядит неуместной в мониторе Авито ровно до той ночи, когда
    компьютер уснул в 20:39, а бот вышел утром и не вернулся. Автозапуск от
    этого не спасает: пока машина спит, никто ничего не ищет, и по логу это
    выглядит как «просто перестал».
    """
    if os.name != "nt":
        return None, "проверяется только на Windows"
    try:
        # Вывод забирается байтами и раскодируется вручную. С `text=True`
        # Python берёт кодировку системы (на русской Windows - cp1251) и
        # спотыкается о первый же символ псевдографики - причём падает не
        # здесь, а в своём читающем потоке, так что никакой try этого не
        # ловит: на экран вылетает Traceback, а запуск идёт дальше.
        out = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP",
                              "STANDBYIDLE"], capture_output=True, timeout=15)
        raw = out.stdout or b""
        for encoding in ("cp866", "cp1251", "utf-8"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="ignore")

        seconds = parse_powercfg(text)
        if seconds is None:
            return None, "не разобрал ответ powercfg"
        if seconds == 0:
            return True, "спящий режим от сети отключён"
        return False, f"уснёт через {seconds // 60} мин без дела"
    except Exception as exc:
        return None, f"не спросил у Windows ({exc})"


async def sleep_setting_async() -> tuple:
    """То же, но не задерживая опрос Telegram.

    `subprocess.run` останавливает весь цикл событий, пока ждёт ответа. У
    powercfg он приходит быстро, но «быстро» и «никогда» отличаются ровно
    в тот раз, когда что-то пойдёт не так, а сторож связи считает молчание
    опроса поводом перезапустить бота.
    """
    return await asyncio.to_thread(sleep_setting)


async def cmd_selfcheck(update, context):
    """Проверяет разом всё, от чего зависит работа. Одна кнопка вместо пяти.

    Смысл не в том, чтобы собрать проверки в кучу, а в том, чтобы владелец
    видел картину целиком. Порознь каждая отвечает «у меня всё хорошо», и
    поэтому молчащий бот при живых проверках - обычное дело: сломано то,
    чего никто по отдельности не спрашивал.
    """
    init_db()
    await update.message.reply_text("⏳ Проверяю всё по очереди, это займёт полминуты...")

    lines = ["<b>Проверка всего</b>\n"]
    trouble = []

    # 1. Монитор включён?
    if is_enabled():
        lines.append("✅ Монитор включён")
    else:
        lines.append("⚪️ Монитор выключен")
        trouble.append("Нажми «🟢 Включить» - без этого бот не ищет.")

    # 2. Чат владельца известен?
    if get_owner_chat():
        lines.append("✅ Есть куда слать находки")
    else:
        lines.append("❌ Не задан чат владельца")
        trouble.append("Напиши боту /start - он запомнит, куда слать.")

    # 3. Авито читается? Настоящий запрос, а не догадка.
    try:
        html = await fetch_html(build_search_url("перфоратор", "instrument"), "avito")
        found = len(parse_search(html, "avito"))
        if found:
            lines.append(f"✅ Авито читается — {found} карточек")
        else:
            lines.append("❌ Авито отдал страницу, но карточек ноль")
            trouble.append("Похоже на капчу или смену вёрстки. "
                           "Нажми «🔍 Проверить Авито» — там подробности.")
    except Exception as exc:
        lines.append(f"❌ Авито не читается: {str(exc)[:70]}")
        trouble.append("Пока это не починится, находок не будет.")

    # 4. База: копится ли что-нибудь.
    with _connect() as conn:
        day = conn.execute("SELECT COUNT(*) c FROM items WHERE first_seen > ?",
                           (time.time() - 86400,)).fetchone()["c"]
        total = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
    if day:
        lines.append(f"✅ За сутки собрано {day} объявлений (всего {money(total)})")
    elif total:
        lines.append(f"⚠️ За сутки ноль новых, в базе {money(total)}")
        trouble.append("Сутки без единой карточки — либо бот стоял, либо Авито не пускает.")
    else:
        lines.append("⚪️ База пуста — бот ещё не отработал ни одного круга")

    # 5. Канал.
    chat = get_channel_chat()
    if not chat:
        lines.append("⚪️ Канал не настроен")
    else:
        try:
            # Спрашиваем права напрямую, ничего не отправляя. Через
            # «печатает...» проверять нельзя: это действие Telegram у
            # каналов не принимает и у полноправного бота тоже - вышла бы
            # ложная тревога на исправном канале.
            me = await context.bot.get_chat(chat_id=chat)
            member = await context.bot.get_chat_member(
                chat_id=chat, user_id=context.bot.id)
            can_post = getattr(member, "can_post_messages", None)
            if member.status == "creator" or can_post:
                right = "на связи"
            elif member.status != "administrator":
                right = "бот не администратор"
            else:
                right = "нет права публиковать"
            with _connect() as conn:
                waiting = conn.execute(
                    "SELECT COUNT(*) c FROM channel_queue WHERE posted_at IS NULL"
                ).fetchone()["c"]
                posted = conn.execute(
                    "SELECT COUNT(*) c FROM channel_queue WHERE posted_at IS NOT NULL"
                ).fetchone()["c"]
            title = getattr(me, "title", None) or str(chat)
            if right == "на связи":
                lines.append(f"✅ Канал «{title}» на связи — "
                             f"в очереди {waiting}, выложено {posted}")
            else:
                lines.append(f"❌ Канал «{title}»: {right}")
                trouble.append("Канал → Администраторы → бот → включить "
                               "«Публикация сообщений».")
        except Exception as exc:
            lines.append(f"❌ Канал {chat} не отвечает: {str(exc)[:60]}")
            trouble.append("Нажми «📢 Проверить канал» — он скажет, что именно чинить.")

    # 6. Консультант. Ввозится здесь, а не наверху файла: consultant сам
    # опирается на этот модуль, и ссылка наверху замкнула бы круг.
    try:
        import resale_expert
        if resale_expert.available():
            lines.append("✅ Консультант включён")
        else:
            lines.append("⚪️ Консультант выключен — нет ключа Groq")
    except Exception as exc:
        lines.append(f"⚠️ Консультант не отвечает на вопрос о себе ({exc})")

    # 7. Сон компьютера: та самая беда, от которой бот однажды умер на сутки.
    ok, note = await sleep_setting_async()
    if ok is True:
        lines.append(f"✅ Сон: {note}")
    elif ok is False:
        lines.append(f"❌ Сон: {note}")
        trouble.append("Параметры → Система → Питание → «При питании от сети "
                       "переводить в спящий режим» → Никогда. "
                       "Пока компьютер спит, бот не ищет.")
    else:
        lines.append(f"⚪️ Сон: {note}")

    # 8. Скорость обхода - не поломка, но её стоит видеть.
    per_cycle = len(take_queries())
    queries = len(all_queries())
    cycle_min = max(1, int((per_cycle * (REQ_DELAY_MIN + REQ_DELAY_MAX) / 2 + CYCLE_PAUSE) / 60))
    sweep = cycle_min * -(-queries // per_cycle)
    word = plural(queries, "слово", "слова", "слов")
    lines.append(f"ℹ️ Полный обход {queries} {word} — около "
                 f"{sweep // 60} ч {sweep % 60} мин")

    if trouble:
        lines.append("\n<b>Что чинить</b>")
        for i, t in enumerate(trouble, 1):
            lines.append(f"{i}. {t}")
    else:
        lines.append("\n<b>Всё в порядке.</b> Ничего делать не нужно.")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_channel_preview(update, context):
    """Показывает владельцу недельные посты, не выкладывая их.

    Смотреть на свой канал глазами читателя надо до публикации, а не после:
    выложенный пост можно удалить, но те, кто уже прочитал, прочитали.
    """
    init_db()
    digest = build_channel_digest()
    proof = build_sold_proof()

    if not digest and not proof:
        await update.message.reply_text(
            "Показывать пока нечего: за неделю не набралось ни статистики, "
            "ни выложенных находок. Через несколько дней работы будет.")
        return

    await update.message.reply_text(
        "Вот что уйдёт в канал на этой неделе. Здесь это видно только тебе.",
        parse_mode="HTML")
    for text in (digest, proof):
        if text:
            await update.message.reply_text(text, parse_mode="HTML")


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
async def self_test(query: str = "перфоратор", category: str | None = None) -> str:
    """Живая проверка: доходим ли до площадки и понимаем ли выдачу.

    Площадку надо знать заранее, а не угадывать по ходу. Первая версия
    строила адрес с учётом площадки, а дальше всё делала по-авитовски:
    грелась через главную Авито, ждала авитовскую примету и разбирала
    авитовским разбором. Для Wildberries это давало ноль карточек и
    надпись «Авито сменил вёрстку» - при живой и исправной выдаче.

    category передаётся явно, потому что одно и то же слово встречается у
    разных площадок: «apple watch» есть и в авитовской категории, и в
    вэбэшной, и угадывание выбрало бы не ту.
    """
    cat = category if category is not None else category_of(query)
    source = source_of(cat)
    site = "Wildberries" if source == "wb" else "Авито"
    url = build_search_url(query, cat)

    try:
        html = await fetch_html(url, source)
    except avito_browser.BrowserUnavailable as exc:
        return (f"❌ Браузер не поднялся: {exc}\n\n"
                f"Без браузера ни одна площадка не читается — проверено.")
    except AvitoBlocked as exc:
        hint = ("Пропускают только настоящий видимый браузер. Проверь, что окно "
                "браузера открылось: если оно закрыто или бот запущен службой, "
                "будет ровно это." if USE_BROWSER else
                "Сейчас ходим по http (AVITO_FETCH=http), а так не пускают. "
                "Убери эту настройку, чтобы вернуться к браузеру.")
        return (f"🚫 {site} блокирует запросы: {exc}\n"
                f"ходили: {fetch_label()}\n\n{hint}")
    except Exception as exc:
        return f"❌ Не достучались до {site}: {type(exc).__name__}: {exc}"

    out = [
        f"🔍 {site}, запрос «{query}»",
        f"чем ходили: {fetch_label()}",
        f"через что: {proxy_label()}",
        f"страница получена: {len(html):,} символов".replace(",", " "),
    ]

    if source == "wb":
        items = wb_source.parse(html)
        out.append(f"карточек разобрано: {len(items)}")
    else:
        via_json = parse_from_json(html)
        via_dom = parse_from_dom(html)
        items = via_json or via_dom
        out.append(f"через JSON: {len(via_json)} карточек")
        out.append(f"через разметку: {len(via_dom)} карточек"
                   + ("" if BS4_AVAILABLE else " (bs4 не установлен)"))
    out.append("")

    if not items:
        # Отличить заслон от смены вёрстки можно по объёму: настоящая выдача
        # весит за миллион символов, у заслона выходит куцая страница. На
        # сервере 13.08.2026 Wildberries отдал 290 тысяч и ноль карточек, а
        # сообщение винило вёрстку - хотя вёрстка была ни при чём.
        if len(html) < 600_000:
            out.append(f"⚠️ Ноль карточек, и страница подозрительно короткая "
                       f"({len(html):,} симв. вместо миллиона с лишним).".replace(",", " "))
            out.append(f"Похоже, {site} не отдал выдачу этому адресу, "
                       f"а вёрстка тут ни при чём.")
        else:
            out.append(f"⚠️ Ноль карточек, а страница целая. "
                       f"Похоже, {site} сменил вёрстку — нужно поправить разбор.")
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
        out.append(f"• {item['title'][:55]} — {money(item['price'])} ₽")
        out.append(f"  {item['url']}")
    if dropped:
        out.append("")
        out.append("Отсеяно как не то:")
        for item in dropped[:5]:
            out.append(f"  ✗ {item['title'][:55]} — {money(item['price'])} ₽")
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
                source = source_of(cat_key)
                key = stats_key(source, query)
                try:
                    html = await fetch_html(build_search_url(query, cat_key), source)
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
                items = parse_search(html, source)

                # Срез рынка на сегодня считается по всей странице, а не по
                # одним новинкам: свежих объявлений в круге бывает две-три,
                # и медиана по ним ничего не значила бы.
                page_ref = page_median([
                    i for i in items
                    if not is_junk(i["title"]) and matches_query(i["title"], query)
                ])

                fresh = save_items(cat_key, key, items)
                found_total += len(fresh)

                market = market_reference(key, page_ref)

                # Самые свежие - первыми. Если в круге нашлось несколько
                # находок, порядок сообщений решает: пока читаешь про то,
                # что висит третий день, лот пятиминутной давности уходит.
                fresh.sort(key=lambda i: (i.get("age_min") is None,
                                          i.get("age_min") or 0))

                for item in fresh:
                    # Две мерки на одну карточку. Владельцу - строгая: то,
                    # ради чего стоит ехать. Каналу - мягче, и с отставанием
                    # на полчаса, чтобы читатели не перехватывали лот у того,
                    # кто их и собрал.
                    good, note = rate_deal(item, query, cat_key, page_ref, key)
                    if good:
                        try:
                            await send_alert(bot, chat_id, item, cat_key, note, market)
                        except Exception as exc:
                            logger.error(f"Монитор Авито: не отправил алерт ({exc})")

                    if get_channel_chat():
                        ok_channel, ch_note = rate_deal(item, query, cat_key, page_ref, key,
                                                        ratio=CHANNEL_RATIO)
                        if ok_channel:
                            queue_for_channel(item, cat_key, ch_note, market)

                # Очередь смотрится здесь, между запросами: круг идёт
                # четверть часа, и ждать его конца значило бы прибавить эту
                # четверть часа к каждой выкладке.
                try:
                    await post_due_to_channel(bot)
                except Exception as exc:
                    logger.error(f"Канал: очередь не разобрана ({exc})")

                await asyncio.sleep(random.uniform(REQ_DELAY_MIN, REQ_DELAY_MAX))

            logger.info(f"Монитор Авито: круг закрыт, новых карточек {found_total}")

            try:
                await post_weekly(bot)
            except Exception as exc:
                logger.error(f"Канал: недельный пост не собрался ({exc})")

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

    channel = get_channel_chat()
    if channel:
        with _connect() as conn:
            waiting = conn.execute(
                "SELECT COUNT(*) c FROM channel_queue WHERE posted_at IS NULL"
            ).fetchone()["c"]
            posted = conn.execute(
                "SELECT COUNT(*) c FROM channel_queue WHERE posted_at IS NOT NULL"
            ).fetchone()["c"]
        channel_line = (f"📢 Канал: {channel}\n"
                        f"Отставание: {CHANNEL_DELAY // 60} мин, "
                        f"порог −{int((1 - CHANNEL_RATIO) * 100)}%\n"
                        f"В очереди: {waiting}, выложено: {posted}\n\n")
    else:
        channel_line = "📢 Канал не настроен - находки только тебе\n\n"

    await update.message.reply_text(
        f"{'🟢 Монитор работает' if is_enabled() else '⚪️ Монитор выключен'}\n\n"
        f"Регион: {AVITO_REGION}\n\n"
        + channel_line
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


TEST_PREFIX = "avitotest:"


def test_keyboard():
    """Кнопки выбора: что именно проверять.

    Раньше проверка была намертво зашита на «перфоратор», и увидеть по ней
    айфоны было нельзя в принципе - сколько ни нажимай. Проверять же все
    двадцать с лишним слов разом нельзя: это два десятка страниц подряд,
    ровно тот способ, которым мы однажды заработали капчу на целый день.
    Поэтому выбор: одно нажатие - одна страница.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    buttons, row = [], []
    for key, cat in CATEGORIES.items():
        row.append(InlineKeyboardButton(f"{cat['emoji']} {cat['name']}",
                                        callback_data=f"{TEST_PREFIX}{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


async def cmd_avito_test(update, context):
    """Проверка связи с Авито: по названному слову или по выбранной категории."""
    if context.args:
        query = " ".join(context.args)
        await update.message.reply_text(f"⏳ Проверяю Авито по запросу «{query}»...")
        await update.message.reply_text(await self_test(query))
        return

    await update.message.reply_text(
        "Что проверить? Одно нажатие — одна страница выдачи.",
        reply_markup=test_keyboard(),
    )


async def on_test_choice(update, context):
    """Нажали кнопку выбора категории."""
    query_obj = update.callback_query
    # Telegram ждёт подтверждения, иначе на кнопке навсегда останутся часики.
    await query_obj.answer()

    cat_key = query_obj.data[len(TEST_PREFIX):]
    cat = CATEGORIES.get(cat_key)
    if not cat:
        await query_obj.message.reply_text("Не знаю такой категории.")
        return

    query = cat["queries"][0]
    low, high = price_band(cat_key)
    await query_obj.message.reply_text(
        f"⏳ Проверяю «{query}» в коридоре "
        f"{low:,}–{high:,} ₽...".replace(",", " ")
    )
    # Категорию передаём явно: по одному слову её не угадать - «apple watch»
    # есть и у Авито, и у Wildberries, и выбралась бы не та площадка.
    await query_obj.message.reply_text(await self_test(query, cat_key))


# Здесь была register(app) - вторая, никем не вызываемая сборка команд.
# Настоящая живёт в avito_bot.build_app(), и только она работает.
#
# Убрана 13.08.2026, потому что успела соврать: добавленный в неё
# обработчик кнопок выглядел подключённым, а на деле не подключался
# никуда, и кнопка молчала. Заметить это можно было только по молчанию -
# ни ошибки, ни строчки в логах. Второе место, где «тоже подключаются
# команды», - ловушка, а не запас прочности.
