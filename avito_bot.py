# -*- coding: utf-8 -*-
"""
Отдельный бот-перекуп: только монитор Авито, без эксперта по слаботочке.

Живёт на своём токене и в своей базе, запускается независимо от bot_5.py.
Токен обязателен в переменной AVITO_BOT_TOKEN - зашитых ключей здесь нет.
"""
import logging
import os
import sys

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

import avito_monitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("AVITO_BOT_TOKEN", "")

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
    await update.message.reply_text(
        "💰 <b>Монитор Авито — Краснодар</b>\n\n"
        f"Слежу за новыми лотами до {avito_monitor.MAX_PRICE:,} ₽".replace(",", " ") + " "
        "по четырём категориям: инструмент, садовая техника, кондиционеры, "
        "мебель и бытовая техника.\n\n"
        "Первые дни присылаю всё, что укладывается в бюджет — так копится "
        "статистика цен. Дальше остаются только лоты дешевле медианы рынка.\n\n"
        "Начни с «🔍 Проверить Авито» — убедимся, что выдача читается.\n"
        "Потом «🟢 Включить».",
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
    else:
        await update.message.reply_text(
            "Я только слежу за Авито — свободный текст не понимаю.\n"
            "Жми кнопки внизу или /help.",
            reply_markup=keyboard(),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.message.reply_text(
        "📖 <b>Что умею</b>\n\n"
        "Обхожу категории по сортировке «сначала новые», запоминаю всё увиденное "
        "и присылаю только то, чего раньше не было и что дешевле рынка.\n\n"
        "<b>Команды</b>\n"
        "/avito — статус и настройки\n"
        "/avito_on — включить слежку\n"
        "/avito_off — выключить\n"
        "/avito_report — сводка по рынку за сутки\n"
        "/avito_test [запрос] — проверить, читается ли выдача Авито\n"
        "/myid — узнать свой Telegram ID\n\n"
        "Отчёт приходит сам в "
        f"{avito_monitor.DIGEST_HOUR}:00. Категории и ключевые слова правятся "
        "в avito_monitor.py, пороги — через переменные окружения (см. AVITO.md).",
        parse_mode="HTML",
        reply_markup=keyboard(),
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Твой Telegram ID: {update.effective_user.id}")


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=True)


def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    def protect(handler):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not await guard(update):
                return
            await handler(update, context)
        return wrapper

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("myid", myid))
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
