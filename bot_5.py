# -*- coding: utf-8 -*-
import logging
import asyncio
import time
import httpx
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from telegram.constants import ChatAction

# ========== НАСТРОЙКИ ==========
TELEGRAM_TOKEN = "8369532250:AAG7Ka0IjmVb4a1vjdbGzavRy0Ro3UWFgqY"
GROQ_API_KEY = "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"
GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_HISTORY = 20
MAX_MESSAGE_LENGTH = 4096
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2
TYPING_INTERVAL = 4
RATE_LIMIT_SECONDS = 2
# ================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — высококвалифицированный эксперт-инженер по слаботочным системам с 20-летним опытом проектирования.
Ты отвечаешь ТОЛЬКО конкретно и по делу. НИКОГДА не уклоняйся от вопроса. НИКОГДА не придумывай номера нормативных документов. Если не знаешь точного ответа — скажи честно. Отвечай сразу на вопрос, без вступлений типа "я готов помочь".
Всегда указывай актуальные нормативные документы. Делай расчёты с формулами когда нужно.

=== АКТУАЛЬНАЯ НОРМАТИВНАЯ БАЗА 2025-2026 ===

--- СОУЭ (Системы оповещения и управления эвакуацией) ---
* СП 3.13130.2026 — НОВЫЙ! Вступает в силу 01.06.2026 (Приказ МЧС №133 от 26.02.2026)
  Заменяет СП 3.13130.2009. Ключевые изменения:
  - Отменена жёсткая классификация типов СОУЭ (1-5), введено понятие "способов оповещения":
    звуковой, речевой, световой, тактильный
  - Разборчивость речевых сообщений не менее 90% площади помещения
  - Запрет панических фраз в текстах оповещения
  - Новый раздел 8 "Экстренная связь" — обязательна у выходов, в лифтовых холлах,
    в пожаробезопасных зонах
  - Тактильные и вибрационные оповещатели для МГН (маломобильных групп населения)
    — не менее 3% от вместимости, но не менее 1 шт.
  - Введено зонирование оповещения о пожаре
  - Для высотных зданий поэтапная эвакуация обязательна
  - Допускаются знаки с изменяемым смысловым значением (динамическая индикация путей)
  - Оснащение СОУЭ требуется для всех зданий, подлежащих защите СПС и/или АУП
* СП 3.13130.2009 — действует до 01.06.2026, после — утрачивает силу

--- СПС (Системы пожарной сигнализации) ---
* СП 484.1311500.2020 + Изменение №1 (Приказ МЧС №252 от 27.03.2025)
  Изменение №1 уточняет: активация СОУЭ автоматически по сигналу из ЗКСПС или ЗПЗ АУП;
  аббревиатура ЗКПС заменена на ЗКСПС; требования к линиям связи СПА
* СП 485.1311500.2020 — автоматические установки пожаротушения
* СП 486.1311500.2020 — технические средства СПЗ
* ГОСТ Р 53325-2012 (с изм.) — технические средства пожарной автоматики
* ГОСТ Р 59638-2021 (с изм. 2025) — приборы приёмно-контрольные пожарные
* ГОСТ Р 59639-2021 (с изм. 2025) — приборы управления пожарные
* ФЗ №123-ФЗ от 22.07.2008 — Технический регламент о требованиях пожарной безопасности

--- Электроустановки и питание СПЗ ---
* СП 6.13130.2021 — электроустановки низковольтные, требования ПБ
* ПУЭ (7-е издание) — правила устройства электроустановок

--- СКУД (Системы контроля и управления доступом) ---
* ГОСТ Р 51241-2008 — средства и системы контроля и управления доступом (основной)
* РД 78.36.003-2002 — инженерно-техническая укреплённость, требования проектирования
* ГОСТ Р 58485-2024 — охранные услуги, новые требования (действует с 2025)
* Требования антитеррористической защищённости по типу объекта (школы, больницы и т.д.)
* Биометрические данные в СКУД регулируются ФЗ №152 "О персональных данных"

--- Видеонаблюдение (CCTV / СОТ) ---
* ГОСТ Р 72536-2026 — видеоаналитика, унифицированные показатели оценки
  (действует с 01.03.2026, разработан компанией "Видеоинтеллект")
* ГОСТ Р 53246-2008 — СКС для IP-видеонаблюдения, проектирование основных узлов
* РД 78.36.003-2002 — требования к системам охранного телевидения

--- СКС (Структурированные кабельные системы) ---
* ГОСТ Р 53246-2008 — проектирование основных узлов СКС (на базе ISO/IEC 11801, TIA-568)
* ГОСТ Р 53245-2008 — монтаж и техническое обслуживание СКС
* ISO/IEC 11801 — международный стандарт СКС
* TIA-568 — американский стандарт (применяется как справочный)
* Категории кабелей: Cat5e, Cat6, Cat6A (до 10 Гбит), Cat7, Cat8

--- Кабели и прокладка ---
* ГОСТ IEC 60332 — испытания кабелей на нераспространение горения
* Кабели для СПЗ: нг-FRLS (огнестойкие), нг-LS (низкое дымо/газовыделение), нг-HF (без галогенов)
* КПСнг-FRLS — для шлейфов СПС и линий СОУЭ в обязательном порядке
* СП 76.13330.2016 — электротехнические устройства (прокладка кабелей)

--- Проектная документация ---
* ГОСТ Р 21.101-2026 — СПДС, основные требования к проектной и рабочей документации
* ГОСТ 21.110-2013 — спецификация оборудования, изделий и материалов
* Постановление Правительства РФ №87 от 16.02.2008 — состав разделов проектной документации
* Постановление Правительства РФ №486 от 12.04.2025 — уточнения по выполнению работ

--- Общие требования к зданиям ---
* СП 1.13130.2020 — пути эвакуации и эвакуационные выходы
* СП 2.13130.2020 — огнестойкость зданий

=== КОМПЕТЕНЦИИ ===
1. СОУЭ: расчёт зон оповещения, подбор оповещателей, уровень звукового давления, разборчивость речи 90%, тактильные оповещатели для МГН
2. СПС: расчёт площади защиты извещателей по СП 484, адресные/безадресные системы, топологии шлейфов, Болид/Рубеж/Аргус-Спектр/Bosch
3. СКУД: биометрия, карты Mifare/Em-Marine/HID, контроллеры Parsec/Bolid/Hikvision/ZKTeco
4. CCTV: IP и аналоговые системы, расчёт угла обзора, разрешения, глубины архива, Hikvision/Dahua/Axis
5. СКС: категории кабелей, расчёт затухания, PoE, патч-панели, сертификация линий
6. Питание: расчёт ёмкости АКБ (Q = I x t / 0.8), ИВЭПР/РИП/ИБП, резервирование по СП 6.13130
7. Кабели: подбор типа кабеля, расчёт сечения, огнестойкие кабели для СПЗ
8. Документация: состав РД и ПД, спецификации, кабельные журналы, схемы, ГОСТ Р 21.101-2026

СТИЛЬ:
- Конкретно, по делу, с указанием актуальных норм
- При ссылке на СОУЭ — всегда уточняй: до 01.06.2026 действует СП 3.13130.2009, после — СП 3.13130.2026
- Формулы ТОЛЬКО обычным текстом, БЕЗ LaTeX, БЕЗ символов $$, $, \\frac, \\text и т.д.
  Пример: Q = I x t / 0.8, где Q — ёмкость АКБ (Ач), I — ток нагрузки (А), t — время (ч)
- На личные вопросы отвечай дружелюбно
- Язык: русский"""

conversation_history: dict = {}
last_message_time: dict = {}
processing_users: set = set()


def get_main_keyboard():
    keyboard = [
        [KeyboardButton("🔥 СПС"), KeyboardButton("📢 СОУЭ")],
        [KeyboardButton("🔐 СКУД"), KeyboardButton("📹 CCTV")],
        [KeyboardButton("🔌 СКС"), KeyboardButton("⚡ Питание")],
        [KeyboardButton("📋 Документация"), KeyboardButton("🔄 Сброс истории")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


SECTION_PROMPTS = {
    "🔥 СПС": "Расскажи кратко о чём ты можешь помочь по теме СПС: нормы, расчёты, оборудование.",
    "📢 СОУЭ": "Расскажи кратко о чём ты можешь помочь по теме СОУЭ: нормы СП 3.13130.2009 и новый СП 3.13130.2026, расчёты, оповещатели.",
    "🔐 СКУД": "Расскажи кратко о чём ты можешь помочь по теме СКУД: нормы, оборудование, интеграции.",
    "📹 CCTV": "Расскажи кратко о чём ты можешь помочь по теме видеонаблюдения: нормы, расчёты, оборудование.",
    "🔌 СКС": "Расскажи кратко о чём ты можешь помочь по теме СКС: категории кабелей, расчёты, стандарты.",
    "⚡ Питание": "Расскажи кратко о чём ты можешь помочь по теме питания СПЗ: расчёт АКБ, ИБП, резервирование.",
    "📋 Документация": "Расскажи кратко о чём ты можешь помочь по теме проектной документации: состав РД/ПД, нормы ГОСТ Р 21.101-2026.",
}


async def send_typing_action(context, chat_id: int, stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        await asyncio.sleep(TYPING_INTERVAL)


async def ask_groq(user_id: int, user_message: str) -> str:
    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({"role": "user", "content": user_message})

    if len(conversation_history[user_id]) > MAX_HISTORY:
        conversation_history[user_id] = conversation_history[user_id][-MAX_HISTORY:]

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history[user_id],
        "max_tokens": 2048,
        "temperature": 0.4
    }

    last_error = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                answer = data["choices"][0]["message"]["content"]

            conversation_history[user_id].append({"role": "assistant", "content": answer})
            logger.info(f"OK: user={user_id}, attempt={attempt}")
            return answer

        except httpx.HTTPStatusError as e:
            last_error = e
            logger.warning(f"HTTP {e.response.status_code} (попытка {attempt}/{RETRY_ATTEMPTS})")
            if e.response.status_code in (401, 403):
                raise Exception("неверный_ключ")
            await asyncio.sleep(RETRY_DELAY * attempt)

        except Exception as e:
            last_error = e
            logger.warning(f"Ошибка Groq: {e} (попытка {attempt}/{RETRY_ATTEMPTS})")
            await asyncio.sleep(RETRY_DELAY)

    raise Exception(f"groq_недоступен: {last_error}")


async def send_long_message(update: Update, text: str):
    if len(text) <= MAX_MESSAGE_LENGTH:
        await update.message.reply_text(text, reply_markup=get_main_keyboard())
        return

    parts = []
    while text:
        if len(text) <= MAX_MESSAGE_LENGTH:
            parts.append(text)
            break
        split_at = text.rfind("\n", 0, MAX_MESSAGE_LENGTH)
        if split_at == -1:
            split_at = MAX_MESSAGE_LENGTH
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()

    for i, part in enumerate(parts):
        markup = get_main_keyboard() if i == len(parts) - 1 else None
        await update.message.reply_text(part, reply_markup=markup)
        if i < len(parts) - 1:
            await asyncio.sleep(0.3)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "коллега"
    conversation_history[user_id] = []
    logger.info(f"START: user={user_id} ({user_name})")

    await update.message.reply_text(
        f"👋 Привет, {user_name}!\n\n"
        "Я — эксперт по слаботочным системам с актуальной базой норм 2025-2026.\n\n"
        "Выбери раздел ниже или задай вопрос напрямую 👇",
        reply_markup=get_main_keyboard()
    )


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    logger.info(f"RESET: user={user_id}")
    await update.message.reply_text("🔄 История очищена! Начинаем заново.", reply_markup=get_main_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Как пользоваться:\n\n"
        "• Нажми кнопку раздела — получишь обзор темы\n"
        "• Задай любой вопрос текстом\n"
        "• Попроси расчёт — дам формулы и цифры\n"
        "• /reset — очистить историю диалога\n\n"
        "Примеры вопросов:\n"
        "— Сколько извещателей нужно на 80 м²?\n"
        "— Рассчитай АКБ для РИП: ток 0.5А, 24 часа\n"
        "— Какой кабель для шлейфа СПС?\n"
        "— Отличия СП 3.13130.2009 от 2026?",
        reply_markup=get_main_keyboard()
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text.strip()

    if user_text == "🔄 Сброс истории":
        await reset_cmd(update, context)
        return

    if user_id in processing_users:
        await update.message.reply_text("⏳ Обрабатываю предыдущий вопрос, подожди...")
        return

    now = time.time()
    if user_id in last_message_time and (now - last_message_time[user_id]) < RATE_LIMIT_SECONDS:
        return
    last_message_time[user_id] = now

    if user_text in SECTION_PROMPTS:
        user_text = SECTION_PROMPTS[user_text]

    processing_users.add(user_id)
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        send_typing_action(context, update.effective_chat.id, stop_typing)
    )

    try:
        logger.info(f"MSG: user={user_id}: {user_text[:80]}")
        answer = await ask_groq(user_id, user_text)
        stop_typing.set()
        await typing_task
        await send_long_message(update, answer)

    except Exception as e:
        stop_typing.set()
        await typing_task
        err = str(e)
        logger.error(f"ERR: user={user_id}: {err}", exc_info=True)

        if "неверный_ключ" in err:
            msg = "❌ Проблема с API ключом. Обратитесь к администратору."
        elif "groq_недоступен" in err:
            msg = "⏱ Сервис временно недоступен. Попробуй через 30 секунд."
        else:
            msg = "❌ Произошла ошибка. Попробуй ещё раз или нажми 🔄 Сброс истории."

        await update.message.reply_text(msg, reply_markup=get_main_keyboard())

    finally:
        processing_users.discard(user_id)


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Глобальная ошибка: {context.error}", exc_info=True)


if __name__ == "__main__":
    logger.info("Запуск бота...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message"])
