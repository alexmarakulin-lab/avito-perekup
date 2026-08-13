# -*- coding: utf-8 -*-
"""Добыча страниц Авито настоящим браузером.

Зачем понадобилось. Защита Qrator узнаёт клиента не по заголовкам, а по
рукопожатию и по тому, настоящий ли это браузер. Мы перепробовали всё, что
делается без браузера: httpx с браузерными заголовками, curl_cffi с
рукопожатием Chrome один в один, чужой сервер, домашний интернет. Ответ был
один: либо капча, либо вежливая подмена - код 200, полмегабайта разметки, а
вместо выдачи главная страница.

Разбор 12.08.2026 показал, что условий на самом деле три, и порознь ни одно
не работает. Проверено подряд, в один день, с одного и того же интернета:

1. Браузер должен быть видимым. Скрытый режим не проходит ни в каком виде:
   скрытый Chromium, он же в новом режиме и настоящий Chrome скрытым - все
   три получили HTTP 429 и «Доступ ограничен», ноль карточек. Маскировка
   примет (webdriver, plugins, WebGL) ничего не изменила. Окно можно
   свернуть - свёрнутое считается видимым.

2. Браузер должен быть тот, что установлен на компьютере. Встроенный в
   Playwright Chromium на этой машине вообще не запускается: Windows
   отвечает «side-by-side configuration is incorrect», ему не хватает
   системной библиотеки. Поэтому channel="chrome" - обычный Chrome.

3. Профиль должен быть постоянным и прогретым. Чистый браузер, идущий сразу
   на адрес поиска, получает 403 - и видимый, и настоящий. Живой браузер, у
   которого накоплены печенья, ту же страницу открывает свободно. Отсюда
   папка профиля, которая переживает перезапуски, и заход сначала на
   главную: первый заход честно получает капчу и вместе с ней пропуск
   _adcc, а следующий с этим пропуском уже проходит. Замер: главная - 429 и
   7 печений, сразу за ней выдача - 200 и 49 карточек, печений стало 43.

Отсюда же и предупреждение на будущее. Заголовок у настоящей выдачи бывает
обезличенный - «Авито — Объявления на сайте Авито», потому что снимок
страницы делается раньше, чем javascript перепишет заголовок. Судить по
одному заголовку нельзя, иначе живая выдача сойдёт за подмену; поэтому
проверка в мониторе смотрит ещё и на карточки.
"""
import asyncio
import logging
import os

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))

# Прятать ли окно. По умолчанию нет - см. пункт 1 в заголовке файла.
HEADLESS = os.getenv("AVITO_BROWSER_HEADLESS", "0").strip() in ("1", "true", "yes")

# Какой браузер брать. chrome - установленный на компьютере, пустая строка -
# встроенный в Playwright (на этой машине он не запускается, см. пункт 2).
CHANNEL = os.getenv("AVITO_BROWSER_CHANNEL", "chrome").strip() or None

# Папка профиля: печенья, накопленные браузером, должны переживать
# перезапуски бота. Иначе каждый запуск начинается с капчи заново.
PROFILE_DIR = os.getenv("AVITO_BROWSER_PROFILE") or os.path.join(HERE, "browser_profile")

# Сколько ждать страницу и сколько - появления карточек, секунды.
NAV_TIMEOUT = float(os.getenv("AVITO_BROWSER_TIMEOUT", "45")) * 1000
CARDS_TIMEOUT = float(os.getenv("AVITO_BROWSER_CARDS_TIMEOUT", "12")) * 1000

# Прокси браузеру передаётся отдельно: у Playwright свой формат.
PROXY = os.getenv("AVITO_PROXY", "").strip()

REGION = os.getenv("AVITO_REGION", "krasnodar")
HOME_URL = f"https://www.avito.ru/{REGION}"

# Пауза после прогрева: браузеру надо дать разложить печенья, а защите -
# увидеть посетителя, который не прыгает по страницам за миллисекунды.
WARMUP_PAUSE = float(os.getenv("AVITO_BROWSER_WARMUP_PAUSE", "6"))
# Сколько раз пытаться прогреться. Паузы растут: 6, 12, 18 секунд.
WARMUP_TRIES = int(os.getenv("AVITO_BROWSER_WARMUP_TRIES", "3"))

# Мелкие приметы браузера под управлением программы. Сами по себе защиту не
# обманывают (проверено), но и вреда нет.
STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
"""

_lock = asyncio.Lock()
_playwright = None
_context = None
_page = None
_warmed = False


class BrowserUnavailable(RuntimeError):
    """Playwright не установлен или браузер не удалось запустить."""


def _proxy_settings() -> dict | None:
    """Переводит строку вида http://логин:пароль@адрес:порт в вид Playwright."""
    if not PROXY:
        return None
    scheme, _, rest = PROXY.partition("://")
    creds, _, host = rest.rpartition("@")
    out = {"server": f"{scheme}://{host}" if scheme else rest}
    if creds:
        user, _, password = creds.partition(":")
        out["username"], out["password"] = user, password
    return out


def _looks_blocked(status: int, html: str) -> bool:
    """Похоже ли это на заслон. Нужно, чтобы решить, стоит ли прогреться заново.

    Наличие карточек важнее кода ответа: настоящая выдача иногда приходит с
    непривычным заголовком, и судить по нему одному нельзя.
    """
    if 'data-marker="item"' in html:
        return False
    low = html.lower()
    return (status in (403, 429)
            or "доступ ограничен" in low
            or "подтвердите, что вы не робот" in low)


async def _ensure_page():
    """Поднимает браузер при первом обращении и возвращает вкладку."""
    global _playwright, _context, _page

    if not PLAYWRIGHT_AVAILABLE:
        raise BrowserUnavailable(
            "не установлен playwright - запусти «Обновить бота»")

    if _page is not None and not _page.is_closed():
        return _page

    os.makedirs(PROFILE_DIR, exist_ok=True)
    _playwright = await async_playwright().start()
    try:
        # Именно persistent_context, а не launch: обычный запуск каждый раз
        # заводит пустой временный профиль, а нам нужен тот же самый.
        _context = await _playwright.chromium.launch_persistent_context(
            PROFILE_DIR,
            channel=CHANNEL,
            headless=HEADLESS,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1440, "height": 900},
            proxy=_proxy_settings(),
            # Имя браузера не подменяем: настоящий Chrome и так представляется
            # честно, а выдуманная версия рядом с настоящей начинкой - это
            # несовпадение, по которому как раз и ловят.
            args=["--disable-blink-features=AutomationControlled"],
        )
    except Exception as exc:
        await _shutdown_playwright()
        first = str(exc).splitlines()[0][:150]
        # Папку профиля Chrome занимает целиком и никого больше в неё не
        # пускает. Ошибка при этом приходит невнятная - «target has been
        # closed», - и без подсказки её не связать с уже открытым окном.
        if "has been closed" in first or "ProcessSingleton" in str(exc):
            hint = ("профиль браузера занят. Обычно это второе окно бота или "
                    "браузер, оставшийся от прошлой проверки. Закрой лишние окна "
                    "браузера бота и повтори.")
        elif "side-by-side" in str(exc):
            hint = ("встроенному браузеру не хватает системной библиотеки. "
                    "Помогает установка обычного Chrome.")
        elif "spawn UNKNOWN" in str(exc):
            hint = ("окно открыть негде: запускай бота двойным щелчком, "
                    "а не через службу или удалённую консоль.")
        else:
            hint = "браузер не запустился."
        raise BrowserUnavailable(f"{hint}\n({first})") from exc

    _context.set_default_navigation_timeout(NAV_TIMEOUT)
    await _context.add_init_script(STEALTH)
    _page = _context.pages[0] if _context.pages else await _context.new_page()
    logger.info("Монитор Авито: браузер поднят (%s, профиль %s)",
                "скрытый" if HEADLESS else "видимый, окно можно свернуть",
                PROFILE_DIR)
    return _page


async def _shutdown_playwright():
    global _playwright
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:
            pass
        _playwright = None


async def _warmup(page):
    """Заход на главную за пропуском.

    Человек не появляется сразу на странице поиска с фильтрами - он приходит
    на главную. Первый заход честно получает капчу и вместе с ней пропуск
    _adcc; следующий с этим пропуском проходит.

    Заходов делается несколько, с растущими паузами. На чистом профиле одного
    не хватает, и это не редкость: пропуск выдаётся не всегда с первого раза.
    Окно при этом видимое - если в нём висит капча, её можно щёлкнуть руками,
    и профиль прогреется сразу.
    """
    global _warmed
    for attempt in range(1, WARMUP_TRIES + 1):
        try:
            response = await page.goto(HOME_URL, wait_until="domcontentloaded")
            await asyncio.sleep(WARMUP_PAUSE * attempt)
            status = response.status if response else 0
            if not _looks_blocked(status, await page.content()):
                _warmed = True
                logger.debug(f"Монитор Авито: прогрев удался с попытки {attempt}")
                return
            logger.debug(f"Монитор Авито: прогрев, попытка {attempt} - заслон")
        except Exception as exc:
            logger.debug(f"Монитор Авито: прогрев не удался ({exc})")
            return
    # Пропуск всё-таки могли выдать вместе с капчей - попробовать выдачу
    # стоит в любом случае, хуже уже не будет.
    _warmed = True


async def fetch(url: str) -> tuple[int, str]:
    """Открывает адрес во вкладке и отдаёт код ответа и готовую разметку.

    Возвращает ровно то же, что и запрос по http, - пару «код, текст».
    Поэтому разбор выдачи, проверка на капчу и подсчёт карточек в мониторе
    остаются прежними: подменяется только способ добычи страницы.
    """
    # Вкладка одна на всё время работы, поэтому два запроса разом её бы
    # поломали - держим очередь.
    async with _lock:
        page = await _ensure_page()

        if not _warmed:
            await _warmup(page)

        status, html = await _open(page, url)

        # Заслон после прогрева обычно значит, что пропуск протух. Один
        # повтор через главную дешевле, чем ждать следующего круга.
        if _looks_blocked(status, html):
            logger.debug("Монитор Авито: заслон, прогреваюсь заново")
            await _warmup(page)
            status, html = await _open(page, url)

        return status, html


async def _open(page, url: str) -> tuple[int, str]:
    response = await page.goto(url, wait_until="domcontentloaded")
    # Карточки дорисовываются javascript'ом уже после разметки, поэтому
    # забирать содержимое сразу - значит регулярно получать пустую выдачу на
    # ровном месте. Ждём первую карточку, но не насмерть: капча и честно
    # пустая выдача карточек не содержат, и их надо вернуть наверх, а не
    # свалиться в ошибку.
    try:
        await page.wait_for_selector('[data-marker="item"]', timeout=CARDS_TIMEOUT)
    except Exception:
        logger.debug("Монитор Авито: карточки не появились за отведённое время")
    return (response.status if response else 0), await page.content()


async def close():
    """Закрывает браузер. Вызывать при остановке бота."""
    global _context, _page, _warmed
    async with _lock:
        for obj in (_page, _context):
            try:
                if obj is not None:
                    await obj.close()
            except Exception:
                pass
        _page = _context = None
        _warmed = False
        await _shutdown_playwright()


def available() -> bool:
    return PLAYWRIGHT_AVAILABLE


def label() -> str:
    """Как ходим - для диагностики в Telegram."""
    if not PLAYWRIGHT_AVAILABLE:
        return "браузер недоступен (playwright не установлен)"
    return (f"браузер {'скрытый' if HEADLESS else 'видимый'}"
            f", {CHANNEL or 'встроенный'}")
