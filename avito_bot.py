# -*- coding: utf-8 -*-
"""
Бот-перекуп: монитор Авито плюс консультант по сделкам.

Токен обязателен в переменной AVITO_BOT_TOKEN - зашитых ключей здесь нет.
"""
import asyncio
import logging
import os
import sys
import time

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

import avito_monitor
import resale_expert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("AVITO_BOT_TOKEN", "")

# Сколько секунд ждать ответа от Telegram. Умолчание библиотеки - 5, и на
# медленном канале этого не хватает даже на отправку короткого сообщения.
NET_TIMEOUT = int(os.getenv("TELEGRAM_TIMEOUT", "30"))

# Как часто сторож смотрит на опрос и сколько молчания считает поломкой.
# Здоровый опрос отвечает раз в десяток секунд, так что три минуты тишины -
# это уже не заминка на медленном канале, а вставший канал.
WATCHDOG_INTERVAL = int(os.getenv("TELEGRAM_WATCHDOG_INTERVAL", "60"))
WATCHDOG_SILENCE = int(os.getenv("TELEGRAM_WATCHDOG_SILENCE", "180"))

# Обходные пути на случай, когда api.telegram.org с сервера не открывается.
# Касаются только Telegram: запросы к Авито идут напрямую, с российского
# адреса - иначе выдача будет чужого региона и блокировки прилетят быстрее.
#
# TELEGRAM_PROXY   - socks5://user:pass@host:port или http://user:pass@host:port
# TELEGRAM_BASE_URL - свой зеркальный адрес Bot API, например
#                     https://xxx.workers.dev/bot (без токена на конце)
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "") or None
TELEGRAM_BASE_URL = os.getenv("TELEGRAM_BASE_URL", "") or None

# curl различает socks5 и socks5h (у второго имя разрешает прокси), а httpx
# знает только socks5:// и разрешает имя на стороне прокси всегда. Строку,
# подобранную через curl, приводим к понятной библиотеке - иначе запуск
# падает с "Unknown scheme for proxy URL".
if TELEGRAM_PROXY and TELEGRAM_PROXY.startswith("socks5h://"):
    TELEGRAM_PROXY = "socks5://" + TELEGRAM_PROXY[len("socks5h://"):]

# ID владельца через запятую. Пусто - бот открыт всем, кто его найдёт.
# Для личного бота лучше заполнить: иначе чужие люди будут гонять твой парсер.
OWNER_IDS = {
    int(x) for x in os.getenv("AVITO_OWNER_ID", "").replace(" ", "").split(",") if x
}

BTN_STATUS = "⚙️ Статус"
BTN_REPORT = "📊 Отчёт за сутки"
BTN_ON = "🟢 Включить"
BTN_OFF = "⚪️ Выключить"
BTN_TEST = "🔍 Проверить Авито"


def keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_REPORT), KeyboardButton(BTN_STATUS)],
            [KeyboardButton(BTN_ON), KeyboardButton(BTN_OFF)],
            [KeyboardButton(BTN_TEST)],
        ],
        resize_keyboard=True,
    )


def allowed(update: Update) -> bool:
    if not OWNER_IDS:
        return True
    return update.effective_user and update.effective_user.id in OWNER_IDS


async def guard(update: Update) -> bool:
    """Отсекает чужих и заодно подсказывает владельцу его собственный ID."""
    if allowed(update):
        return True
    user_id = update.effective_user.id if update.effective_user else "?"
    logger.warning(f"Отклонён чужой запрос от {user_id}")
    await update.message.reply_text(
        f"Это личный бот.\n\nЕсли он твой — добавь свой ID в AVITO_OWNER_ID: {user_id}"
    )
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    avito_monitor.init_db()
    expert_line = (
        "\n\n💬 <b>И просто пиши мне вопросы.</b> Подскажу, сколько реально стоит лот, "
        "что проверить перед покупкой, с какой суммы торговаться и что останется "
        "на руки. Считаю по цифрам из собственной базы, а не наугад."
        if resale_expert.available() else
        "\n\n<i>Консультант выключен: не задан GROQ_API_KEY.</i>"
    )
    await update.message.reply_text(
        "💰 <b>Перекуп — Краснодар</b>\n\n"
        f"Слежу за новыми лотами до {avito_monitor.MAX_PRICE:,} ₽".replace(",", " ") + " "
        "по четырём категориям: инструмент, садовая техника, кондиционеры, "
        "мебель и бытовая техника.\n\n"
        "Первые дни присылаю всё, что укладывается в бюджет — так копится "
        "статистика цен. Дальше остаются только лоты дешевле медианы рынка.\n\n"
        "Начни с «🔍 Проверить Авито» — убедимся, что выдача читается.\n"
        "Потом «🟢 Включить»." + expert_line,
        parse_mode="HTML",
        reply_markup=keyboard(),
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    text = (update.message.text or "").strip()
    if text == BTN_STATUS:
        await avito_monitor.cmd_avito(update, context)
    elif text == BTN_REPORT:
        await avito_monitor.cmd_avito_report(update, context)
    elif text == BTN_ON:
        await avito_monitor.cmd_avito_on(update, context)
        await update.message.reply_text("Клавиатура на месте.", reply_markup=keyboard())
    elif text == BTN_OFF:
        await avito_monitor.cmd_avito_off(update, context)
    elif text == BTN_TEST:
        context.args = []
        await avito_monitor.cmd_avito_test(update, context)
    elif text:
        await ask_expert(update, context, text)


async def ask_expert(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str):
    """Свободный текст уходит консультанту по перекупу."""
    user_id = update.effective_user.id
    if resale_expert.rate_limited(user_id):
        await update.message.reply_text("⏳ Не так быстро, дай ответить на предыдущий.")
        return

    stop = asyncio.Event()

    async def typing():
        while not stop.is_set():
            try:
                await context.bot.send_chat_action(
                    chat_id=update.effective_chat.id, action=ChatAction.TYPING
                )
            except Exception:
                pass
            await asyncio.sleep(4)

    task = asyncio.ensure_future(typing())
    try:
        answer = await resale_expert.ask(user_id, question)
    finally:
        stop.set()
        task.cancel()

    for chunk in split_message(answer):
        await update.message.reply_text(chunk, reply_markup=keyboard())


def split_message(text: str, limit: int = 4000) -> list:
    """Телеграм не принимает сообщения длиннее 4096 символов."""
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.message.reply_text(
        "📖 <b>Что умею</b>\n\n"
        "<b>1. Слежу за Авито.</b> Обхожу категории по сортировке «сначала новые», "
        "запоминаю всё увиденное и присылаю только то, чего раньше не было "
        "и что дешевле медианы рынка.\n\n"
        "<b>2. Консультирую по сделке.</b> Просто напиши вопрос: «перфоратор бош "
        "за 3500, брать?», «что смотреть в б/у стиралке», «как сбить цену на диван». "
        "Где по теме есть накопленная статистика — считаю по ней.\n\n"
        "<b>Команды</b>\n"
        "/avito — статус и настройки\n"
        "/avito_on — включить слежку\n"
        "/avito_off — выключить\n"
        "/avito_report — сводка по рынку за сутки\n"
        "/avito_test [запрос] — проверить, читается ли выдача Авито\n"
        "/reset — забыть текущий диалог\n"
        "/myid — узнать свой Telegram ID\n\n"
        "Отчёт приходит сам в "
        f"{avito_monitor.DIGEST_HOUR}:00. Категории и ключевые слова правятся "
        "в avito_monitor.py, пороги — через переменные окружения (см. AVITO.md).",
        parse_mode="HTML",
        reply_markup=keyboard(),
    )


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    resale_expert.reset(update.effective_user.id)
    await update.message.reply_text("🔄 Диалог забыт. База лотов и цен не тронута.",
                                    reply_markup=keyboard())


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Твой Telegram ID: {update.effective_user.id}")


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=True)


class WatchedRequest(HTTPXRequest):
    """Соединение для опроса, отмечающее время последнего ответа.

    Библиотека держит два раздельных набора соединений: одни для опроса
    getUpdates, другие для всего остального. Это как две телефонные линии
    в одну контору. Линия опроса обрывается молча - библиотека остаётся
    ждать ответа, который не придёт, - а справочная при этом отвечает.

    Поэтому следить надо именно за той линией, которая ломается. Здесь и
    отмечается каждый успешный ответ на опрос.
    """

    last_ok = 0.0

    async def do_request(self, *args, **kwargs):
        result = await super().do_request(*args, **kwargs)
        WatchedRequest.last_ok = time.monotonic()
        return result


async def watchdog(app):
    """Сторож опроса Telegram.

    Здоровый опрос отвечает раз в десяток секунд без перерыва. Если ответов
    нет дольше положенного - канал встал, и никакие проверки связи этого не
    покажут: бот молчит, а Telegram отвечает.

    Прежний сторож звонил в справочную командой get_me, слышал «алло» и
    обнулял счётчик неудач. Поэтому бот однажды молчал одиннадцать часов
    подряд при нулевом счётчике обращений - сторож всё это время был
    доволен.
    """
    WatchedRequest.last_ok = time.monotonic()
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL)
        silence = time.monotonic() - WatchedRequest.last_ok
        if silence < WATCHDOG_SILENCE:
            continue
        logger.error(
            f"Сторож: опрос молчит {int(silence)} с при пороге {WATCHDOG_SILENCE} с, "
            "перезапускаю бота"
        )
        # Именно жёсткий выход: обычная остановка попробует корректно закрыть
        # те самые зависшие соединения и повиснет вместе с ними.
        os._exit(1)


def build_app():
    # Лимиты ожидания подняты с умолчательных 5 секунд: канал до Telegram
    # с российского хостинга живой, но медленный, и отправка ответа не
    # укладывалась в стандартный таймаут - бот получал команды и молчал.
    builder = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(NET_TIMEOUT)
        .read_timeout(NET_TIMEOUT)
        .write_timeout(NET_TIMEOUT)
        .pool_timeout(NET_TIMEOUT)
    )

    if TELEGRAM_PROXY:
        builder = builder.proxy(TELEGRAM_PROXY)
        logger.info("Telegram: работаю через прокси")

    # Соединение для опроса собирается вручную: только так к нему можно
    # приставить сторожа. Настройки те же, что были у builder-а, поэтому
    # поведение не меняется - меняется лишь то, что теперь видно, жив ли
    # опрос. Библиотека запрещает задавать и своё соединение, и отдельные
    # лимиты ожидания для опроса, поэтому вторые убраны выше.
    updates_settings = dict(
        connection_pool_size=1,
        connect_timeout=NET_TIMEOUT,
        write_timeout=NET_TIMEOUT,
        pool_timeout=NET_TIMEOUT,
        read_timeout=NET_TIMEOUT + 20,
    )
    if TELEGRAM_PROXY:
        updates_settings["proxy"] = TELEGRAM_PROXY
    try:
        builder = builder.get_updates_request(WatchedRequest(**updates_settings))
        logger.info(f"Сторож: слежу за опросом, порог молчания {WATCHDOG_SILENCE} с")
    except Exception as exc:
        # Запасной путь на случай, если библиотека сменит устройство. Бот
        # поднимется как раньше, просто без присмотра за опросом.
        logger.warning(f"Сторож: не смог приставить наблюдателя к опросу ({exc})")
        builder = (
            builder
            .get_updates_connect_timeout(NET_TIMEOUT)
            .get_updates_write_timeout(NET_TIMEOUT)
            .get_updates_pool_timeout(NET_TIMEOUT)
            .get_updates_read_timeout(NET_TIMEOUT + 20)
        )
        if TELEGRAM_PROXY:
            builder = builder.get_updates_proxy(TELEGRAM_PROXY)

    if TELEGRAM_BASE_URL:
        base = TELEGRAM_BASE_URL.rstrip("/")
        builder = builder.base_url(f"{base}/bot").base_file_url(f"{base}/file/bot")
        logger.info(f"Telegram: зеркало Bot API {base}")

    app = builder.build()

    def protect(handler):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not await guard(update):
                return
            await handler(update, context)
        return wrapper

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("reset", protect(reset_cmd)))
    app.add_handler(CommandHandler("avito", protect(avito_monitor.cmd_avito)))
    app.add_handler(CommandHandler("avito_on", protect(avito_monitor.cmd_avito_on)))
    app.add_handler(CommandHandler("avito_off", protect(avito_monitor.cmd_avito_off)))
    app.add_handler(CommandHandler("avito_report", protect(avito_monitor.cmd_avito_report)))
    app.add_handler(CommandHandler("avito_test", protect(avito_monitor.cmd_avito_test)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_button))
    app.add_error_handler(error_handler)

    async def _post_init(application):
        avito_monitor.init_db()
        application.create_task(avito_monitor.monitor_loop(application.bot))
        logger.info("Монитор Авито: фоновая задача запущена")
        application.create_task(watchdog(application))
        logger.info(
            f"Сторож опроса: смотрит раз в {WATCHDOG_INTERVAL} с, "
            f"перезапуск после {WATCHDOG_SILENCE} с молчания"
        )

    app.post_init = _post_init
    return app


if __name__ == "__main__":
    if not BOT_TOKEN:
        print(
            "Не задан AVITO_BOT_TOKEN.\n\n"
            "1. Открой @BotFather в Telegram, команда /newbot\n"
            "2. Придумай имя и username (должен заканчиваться на bot)\n"
            "3. Скопируй выданный токен\n"
            "4. Запусти так:\n"
            "   AVITO_BOT_TOKEN=<токен> python3 avito_bot.py",
            file=sys.stderr,
        )
        sys.exit(1)

    if not OWNER_IDS:
        logger.warning(
            "AVITO_OWNER_ID не задан — бот ответит любому, кто его найдёт. "
            "Узнай свой ID командой /myid и пропиши в переменную."
        )

    logger.info("Запуск бота-перекупа...")
    build_app().run_polling(drop_pending_updates=True, allowed_updates=["message"])
