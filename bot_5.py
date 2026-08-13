# -*- coding: utf-8 -*-
import logging
import asyncio
import math
import re
import time
import io
import httpx
try:
    import fitz  # pymupdf
    FITZ_AVAILABLE = True
except ImportError:
    FITZ_AVAILABLE = False
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from telegram.constants import ChatAction

# ========== НАСТРОЙКИ ==========
TELEGRAM_TOKEN = "8369532250:AAG7Ka0IjmVb4a1vjdbGzavRy0Ro3UWFgqY"
GROQ_API_KEY = "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"

GROQ_MODELS = [
    "llama-3.3-70b-versatile",  # основная - умная, качественные ответы
    "llama-3.1-70b-versatile",  # резерв 1
    "mixtral-8x7b-32768",       # резерв 2
    "gemma2-9b-it",             # резерв 3 - слабее, но всегда доступна
]

SERPER_API_KEY = ""  # ВСТАВЬ КЛЮЧ с serper.dev (бесплатно 2500 запросов)
MAX_HISTORY = 6          # СНИЖЕНО с 20 до 6 - экономия токенов
MAX_MESSAGE_LENGTH = 4096
RETRY_ATTEMPTS = 2       # СНИЖЕНО с 3 до 2
RETRY_DELAY = 3
TYPING_INTERVAL = 4
RATE_LIMIT_SECONDS = 3   # УВЕЛИЧЕНО с 2 до 3 - защита от спама
MAX_PDF_CHARS = 4000     # СНИЖЕНО с 6000 до 4000 - экономия токенов
MAX_TOKENS = 1024        # СНИЖЕНО с 2048 до 1024 - вдвое меньше расхода
KADASTR_TIMEOUT = 20     # таймаут запросов к НСПД / ПКК Росреестра
# nspd.gov.ru и pkk.rosreestr.ru живут на сертификатах Минцифры. Если ловишь SSLError -
# поставь корневой сертификат Минцифры в систему; отключать проверку - крайний случай.
KADASTR_VERIFY_SSL = True
# ================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ========== МОДУЛЬНЫЙ ПРОМПТ ==========
# Базовая часть - всегда включается (~400 токенов вместо 3500)
SYSTEM_BASE = """Ты - многопрофильный эксперт-практик с 20-летним опытом. Отвечаешь как опытный коллега, а не как учебник.

ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА:
1. Только конкретика: цифры, температуры, сроки, объёмы. Никаких "может указывать", "обычно", "как правило".
2. Никогда не пересказывай человеку то, что он только что сам написал. Если он цитирует текст и спрашивает "как понять?" - дай практический смысл, а не пересказ по пунктам.
3. Не делай нумерованные списки там, где можно ответить одним абзацем.
4. Если вопрос простой - ответ короткий. Не раздувай.
5. Формулы ТОЛЬКО обычным текстом, БЕЗ LaTeX, БЕЗ $$, $, \\frac.
6. Язык: русский. Никогда не придумывай номера нормативных документов.

ПРИМЕР ПЛОХОГО ОТВЕТА на "как понять что пиво прошло карбонизацию":
"1. Пенистость: если пиво стало более пенистым... 2. Газированность: если пиво шипит..."

ПРИМЕР ХОРОШЕГО ОТВЕТА:
"Сожми пластиковую бутылку - если твёрдая как камень, готово. Со стеклом: открой тестовую бутылку через 7 дней - должно зашипеть и дать пену. Если газа мало - ещё 3-5 дней при 20 C."
"""

# Модули - подключаются только при необходимости
MODULES = {
    "fire": """== СЛАБОТОЧНЫЕ СИСТЕМЫ: СПС и СОУЭ ==
СП 484.1311500.2020 + Изм.N1 (2025) - СПС; СП 3.13130.2026 (с 01.06.2026) / СП 3.13130.2009 (до 01.06.2026) - СОУЭ.
СП 3.13130.2026: отменена классификация типов 1-5, разборчивость речи >= 90%, тактильные оповещатели для МГН (3% вместимости, не менее 1 шт.), экстренная связь у выходов, зонирование оповещения.
Формула УЗД: Lp2 = Lp1 + 20 lg (r1 / r2), где r1=1м.
Расчёт АКБ: Q = I x t / 0.8 (Ач).
Кабели СПЗ: КПСнг-FRLS.""",

    "skud_cctv": """== СКУД и CCTV ==
СКУД: ГОСТ Р 51241-2008, ГОСТ Р 58485-2024, РД 78.36.003-2002. Идентификаторы: Mifare, Em-Marine, HID, биометрия (ФЗ N152). Контроллеры: Parsec, Bolid, Hikvision, ZKTeco.
CCTV: ГОСТ Р 72536-2026 (видеоаналитика, с 01.03.2026), ГОСТ Р 53246-2008, РД 78.36.003-2002. Оборудование: Hikvision, Dahua, Axis.""",

    "sks_docs": """== СКС и Документация ==
СКС: ГОСТ Р 53246-2008, ГОСТ Р 53245-2008, ISO/IEC 11801, TIA-568. Категории: Cat5e, Cat6, Cat6A (10 Гбит), Cat7, Cat8.
Документация: ГОСТ Р 21.101-2026, ГОСТ 21.110-2013, Постановление N87 от 16.02.2008, Постановление N486 от 12.04.2025.""",

    "brewing": """== ПИВОВАРЕНИЕ ==
Температурные паузы: ферулиновая 45 C 15мин (гвоздика!), бета-амилаза 62-65 C 40-60мин, альфа-амилаза 68-72 C 20-30мин, мэш-аут 78 C 10мин.
Формулы: IBU = (масса хмеля г x альфа% x утилизация%) / (объём л x 0.1); ABV = (OG - FG) x 131.25; AA = (OG-FG)/(OG-1.000)x100%.
Карбонизация элей: 2.3-2.6 объёма CO2, пшеничного: 2.5-2.8. Сухое охмеление: 18-20 C, 3-7 дней.

КРОЗЕНИНГ (праймер суслом без сахара):
Крозенинг - добавление недоброженного сусла вместо сахара для карбонизации в бутылке.
Расчёт объёма крозена: V_крозен = (CO2_цель - CO2_остаток) x V_пива / (OG_крозен - 1.000) / 0.5
Упрощённая формула на практике: 10-15% от объёма пива при OG крозена 1.040-1.050.
Пример: на 20 л пива берём 2-3 л сусла с OG 1.040-1.050, которое ещё не начало бродить.
Сусло для крозена: взять в день варки, заморозить или хранить в холодильнике до момента розлива.
Добавлять в пиво при температуре 18-20 C, когда пиво уже достигло FG.
Выдержка в бутылке та же - 14-21 день при 20 C.
Плюсы: натуральный вкус, никаких посторонних сахаров. Минус: нужно точно знать OG сусла.""",

    "distill": """== САМОГОНОВАРЕНИЕ И ДИСТИЛЛЯЦИЯ ==
Зерновые браги: осахаривание 60-65 C 2-4ч, охладить до 28-30 C перед дрожжами. 
Перегонка: 1й - весь СС до 2-5% в струе; 2й дробный - головы 50-100мл с 10л браги (ацетоновый запах), тело до 45-50% в струе, хвосты - мутнеют с водой.
Разбавление: V_воды = V_спирта x (C_нач - C_конеч) / C_конеч.
Выдержка: дубовая щепа Medium toast 2-5 г/л, 2-4 недели.""",

    "recipe_weizen": """== РЕЦЕПТ: Ur-Weizen Clone (Kurpfalz Brau) ==
Hefeweizen, 23 л, ABV 4.2-4.4%, OG 1.043-1.045, FG ~1.011, IBU 11-12, EBC ~8.
ЗАСЫПЬ 4.30 кг: пшеничный солод 2.25 кг (52%), Pilsner 1.45 кг (34%), Munich 1 0.30 кг (7%), овсяные хлопья 0.20 кг (5%), Caramunich 0.10 кг (2%). Рисовая шелуха 150-250 г.
ХМЕЛЬ: 60 мин Tettnanger 12 г (alpha6%), 15 мин Tettnanger 7 г.
ДРОЖЖИ: Lallemand Munich Classic 11 г.
ПРОФИЛЬ: нагреть воду до 48 C, засыпать солод -> 45 C 15мин (ферулиновая!) -> 62 C 30мин -> 72 C 30мин + йодный тест -> 78 C 10мин -> промывка 76-78 C ~16л.
БРОЖЕНИЕ: 18-19 C дни 1-3, 20-21 C дни 4-7, FG стабильна 48ч -> разлив.
ПРАЙМЕР: 6.5 г/л декстрозы (обычные бутылки), 7.0 г/л (вайценовые). Выдержка 20-22 C 14 дней.""",

    "land": """== ЗЕМЕЛЬНЫЕ УЧАСТКИ: ПОКУПКА И ПРОВЕРКА ==
Ты оцениваешь участок как перекупщик, а не как риелтор продавца: ищешь причины НЕ покупать.
ЮРИДИКА: береговая полоса 20 м (ст. 6 ВК РФ) в собственность не оформляется никогда. Водоохранная зона рек: 50 м (до 10 км длины), 100 м (10-50 км), 200 м (свыше 50 км) - строить можно, выгребная яма нельзя. Зона затопления (ЗОУИТ, ст. 67.1 ВК РФ п.6) - строительство жилья ЗАПРЕЩЕНО, ипотеки и страховки не будет. Наложение на земли лесного фонда - в предгорьях массово.
ДОКУМЕНТЫ: категория "земли населённых пунктов" + ВРИ ИЖС/ЛПХ приусадебный = дом регистрируется. "Земли сельхозназначения" вне населённого пункта = дом не зарегистрировать. Проверять: выписка ЕГРН об объекте, межевание (границы установлены), обременения, аресты, срок владения продавца.
СЕТИ: льготное техприсоединение 550 руб. работает при опоре в пределах 300 м, дальше от 300 тыс. Газ в горных сёлах Краснодарского края отсутствует и не планируется.
ЦЕНЫ по предгорьям Краснодарского края (тыс. руб/сотка, ЛПХ и ИЖС без коммуникаций): Северский р-н 40-90; Горячий Ключ 25-60; Апшеронский р-н 8-35; Мостовский р-н 8-30. Цены на агрегаторах завышены на 20-40%.
РИСКИ МЕСТНОСТИ: паводки на Пшехе, Псекупсе, Курджипсе (подъём 3-5 м за ночь), оползни на склонах Апшеронского района, зимой непроезжие грунтовки, в Мезмае и Гуамке часть земель в границах памятников природы - застройка запрещена.
Никогда не подтверждай "хороший участок" без данных ЕГРН. Всегда называй конкретную проверку, которую надо сделать.""",
}

# Ключевые слова для определения нужного модуля
MODULE_KEYWORDS = {
    "fire": ["спс", "соуэ", "сигнализац", "оповещ", "пожар", "извещател", "шлейф", "акб", "ивэпр", "рип",
             "sp 484", "sp 3", "кпсн", "frls", "болид", "рубеж", "аргус", "питани", "резерв"],
    "skud_cctv": ["скуд", "доступ", "камер", "видеонаблюд", "cctv", "сот", "биометр", "mifare",
                  "hikvision", "dahua", "axis", "parsec", "zkteco", "турникет"],
    "sks_docs": ["скс", "кабел", "cat5", "cat6", "cat7", "cat8", "документ", "проект", "рд ", "пд ",
                 "спецификац", "чертёж", "патч", "poe", "витая пара", "гост 21"],
    "brewing": ["пиво", "пивовар", "солод", "хмел", "дрожж", "затир", "сусло", "ферментац",
                "ibu", "og ", "fg ", "abv", "ebc", "ipa", "стаут", "лагер", "эль", "weizen",
                "вайцен", "карбонизац", "охмелен"],
    "distill": ["самогон", "дистиллят", "перегон", "брага", "спирт", "виски", "бурбон",
                "кальвадос", "чача", "граппа", "сливовиц", "головы", "хвосты", "куб",
                "колонн", "флегм", "ректификац"],
    "recipe_weizen": ["ur-weizen", "ur weizen", "weizen", "вайцен", "пшеничн", "kurpfalz",
                      "мой рецепт", "ферулиновая"],
    "land": ["участок", "участка", "соток", "сотка", "кадастр", "егрн", "росреестр", "ижс",
             "лпх", "снт", "днп", "межеван", "зоуит", "водоохран", "затоплен", "подтоплен",
             "лесной фонд", "земл", "авито", "перекуп", "пшеха", "псекупс", "курджипс",
             "апшеронск", "мостовск", "северск", "горячий ключ", "росреестра"],
}


def select_modules(text: str) -> list:
    """Определяет нужные модули по ключевым словам в тексте пользователя."""
    text_lower = text.lower()
    selected = []
    for module, keywords in MODULE_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            selected.append(module)
    return selected


def build_system_prompt(modules: list, pdf_text: str = "", extra_context: str = "") -> str:
    """Собирает системный промпт только из нужных модулей."""
    parts = [SYSTEM_BASE]
    for m in modules:
        if m in MODULES:
            parts.append(MODULES[m])
    if pdf_text:
        parts.append(f"=== ЗАГРУЖЕННЫЙ PDF ===\n{pdf_text}\n=== КОНЕЦ PDF ===")
    if extra_context:
        parts.append(extra_context)
    return "\n\n".join(parts)


SECTION_TEXTS = {
    "🔥 СПС": (
        "🔥 Системы пожарной сигнализации (СПС)\n\n"
        "Актуальные нормы:\n"
        "* СП 484.1311500.2020 + Изменение N1 (2025)\n"
        "* СП 486.1311500.2020\n"
        "* ГОСТ Р 53325-2012\n\n"
        "Чем могу помочь:\n"
        "- Расчёт количества и расстановки извещателей\n"
        "- Выбор типа системы: адресная или безадресная\n"
        "- Топология шлейфов и линий связи\n"
        "- Подбор оборудования: Болид, Рубеж, Аргус-Спектр, Bosch\n"
        "- Требования к кабелям СПС\n"
        "- Интеграция с СОУЭ и АУП\n\n"
        "Задавай вопрос!"
    ),
    "📢 СОУЭ": (
        "📢 Системы оповещения и управления эвакуацией (СОУЭ)\n\n"
        "Актуальные нормы:\n"
        "* до 01.06.2026 - СП 3.13130.2009\n"
        "* с 01.06.2026 - СП 3.13130.2026 (новый!)\n\n"
        "Ключевые изменения в СП 3.13130.2026:\n"
        "- Отменена классификация типов 1-5, введены способы оповещения\n"
        "- Разборчивость речи не менее 90% площади\n"
        "- Тактильные оповещатели для МГН (не менее 3% вместимости)\n"
        "- Обязательная экстренная связь у выходов и в лифтовых холлах\n\n"
        "Задавай вопрос!"
    ),
    "🔐 СКУД": (
        "🔐 Системы контроля и управления доступом (СКУД)\n\n"
        "Актуальные нормы:\n"
        "* ГОСТ Р 51241-2008 - основной стандарт\n"
        "* ГОСТ Р 58485-2024 (действует с 2025)\n"
        "* РД 78.36.003-2002\n"
        "* ФЗ N152 - при использовании биометрии\n\n"
        "Задавай вопрос!"
    ),
    "📹 CCTV": (
        "📹 Системы видеонаблюдения (CCTV / СОТ)\n\n"
        "Актуальные нормы:\n"
        "* ГОСТ Р 72536-2026 - видеоаналитика (с 01.03.2026)\n"
        "* ГОСТ Р 53246-2008\n"
        "* РД 78.36.003-2002\n\n"
        "Задавай вопрос!"
    ),
    "🔌 СКС": (
        "🔌 Структурированные кабельные системы (СКС)\n\n"
        "Актуальные нормы:\n"
        "* ГОСТ Р 53246-2008 - проектирование\n"
        "* ГОСТ Р 53245-2008 - монтаж и обслуживание\n"
        "* ISO/IEC 11801, TIA-568\n\n"
        "Задавай вопрос!"
    ),
    "⚡ Питание": (
        "⚡ Питание систем пожарной защиты (СПЗ)\n\n"
        "Актуальные нормы:\n"
        "* СП 6.13130.2021\n"
        "* ПУЭ 7-е издание\n\n"
        "Формула расчёта АКБ:\n"
        "Q = I x t / 0.8\n"
        "где Q - ёмкость (Ач), I - ток нагрузки (А), t - время резервирования (ч)\n\n"
        "Задавай вопрос! Например: ток 0.5А, резерв 24 часа - какой АКБ?"
    ),
    "📋 Документация": (
        "📋 Проектная документация (РД и ПД)\n\n"
        "Актуальные нормы:\n"
        "* ГОСТ Р 21.101-2026 - основные требования\n"
        "* ГОСТ 21.110-2013 - спецификации\n"
        "* Постановление N87 от 16.02.2008\n"
        "* Постановление N486 от 12.04.2025\n\n"
        "Задавай вопрос!"
    ),
    "🍺 Пивоварение": (
        "🍺 Пивоварение - профессиональный уровень\n\n"
        "Чем могу помочь:\n"
        "- Разработка рецептуры под стиль (BJCP/BA)\n"
        "- Расчёт IBU, OG, FG, ABV, цвета (EBC/SRM)\n"
        "- Технология затирания: паузы, температуры, pH\n"
        "- Подбор солода, хмеля, дрожжей\n"
        "- Водоподготовка, ферментация, карбонизация\n\n"
        "Задавай вопрос!"
    ),
    "🥃 Дистилляция": (
        "🥃 Самогоноварение и дистилляция\n\n"
        "Чем могу помочь:\n"
        "- Рецепты браг: зерновые, фруктовые, сахарные\n"
        "- Расчёт выхода спирта и крепости\n"
        "- Технология перегонки на Pot Still и колонне\n"
        "- Правильное дробление: головы / тело / хвосты\n"
        "- Выдержка в бочке и на щепе\n\n"
        "Задавай вопрос!"
    ),
    "🌾 Рецепты браг": (
        "🌾 Рецепты браг и сусла\n\n"
        "Доступные направления:\n"
        "ЗЕРНОВЫЕ: кукурузная (Bourbon), ржаная, солодовая ячменная, пшеничная\n"
        "ФРУКТОВЫЕ: яблочная (Кальвадос), виноградная (Чача), сливовая (Сливовица)\n"
        "ПИВНОЕ СУСЛО: Пилснер, IPA, Stout, Weizen, Бельгийские стили\n\n"
        "Нажми 🍋 Ur-Weizen - мой рецепт клона Kurpfalz Brau\n\n"
        "Скажи, что хочешь сварить - дам полный рецепт с расчётами!"
    ),
    "🍋 Ur-Weizen": (
        "🍋 Ur-Weizen Clone - Kurpfalz Brau\n"
        "Hefeweizen * 23 л * ABV 4,2-4,4%\n\n"
        "--- ПАРАМЕТРЫ ---\n"
        "OG: 1.043-1.045 | FG: ~1.011 | IBU: 11-12 | EBC: ~8 | CO2: 3,2-3,8 об.\n\n"
        "--- ЗАСЫПЬ (4,30 кг) ---\n"
        "* Пшеничный солод - 2,25 кг (52%)\n"
        "* Pilsner - 1,45 кг (34%)\n"
        "* Munich 1 - 0,30 кг (7%)\n"
        "* Овсяные хлопья - 0,20 кг (5%)\n"
        "* Caramunich - 0,10 кг (2%)\n"
        "+ Рисовая шелуха 150-250 г\n\n"
        "--- ХМЕЛЬ ---\n"
        "* 60 мин: Tettnanger - 12 г (alpha 6%)\n"
        "* 15 мин: Tettnanger - 7 г\n\n"
        "--- ДРОЖЖИ ---\n"
        "Lallemand Munich Classic - 1 пакет 11 г\n\n"
        "--- ПРОФИЛЬ ЗАТИРАНИЯ ---\n"
        "1. Вода 48 C -> засыпать солод\n"
        "2. Ферулиновая пауза: 45 C - 15 мин (КРИТИЧНО для гвоздики!)\n"
        "3. Подъём до 62 C (~1 C/мин)\n"
        "4. Бета-амилаза: 62 C - 30 мин\n"
        "5. Подъём до 72 C\n"
        "6. Альфа-амилаза: 72 C - 30 мин + йодный тест\n"
        "7. Мэш-аут: 78 C - 10 мин\n"
        "8. Промывка 76-78 C ~16 л\n\n"
        "--- БРОЖЕНИЕ ---\n"
        "Дни 1-3: 18-19 C | Дни 4-7: 20-21 C\n"
        "FG ~1.011 стабильна 48 ч -> разлив\n\n"
        "--- ПРАЙМЕР ---\n"
        "Обычные бутылки: 6,5 г/л = 150 г на 23 л\n"
        "Вайценовые: 7,0 г/л = 161 г на 23 л\n"
        "Выдержка: 20-22 C, 14 дней\n\n"
        "Есть вопросы по рецепту? Задавай!"
    ),

    "🏞 Участок": (
        "🏞 ПРОВЕРКА ЗЕМЕЛЬНОГО УЧАСТКА\n\n"
        "Пришли кадастровый номер - подниму данные ЕГРН и скажу, что не так.\n"
        "Формат: 23:02:0209000:1629\n\n"
        "Ещё лучше - скопируй объявление целиком (описание + цена + площадь). "
        "Тогда посчитаю цену за сотку, сравню с рынком района "
        "и подсвечу опасные формулировки продавца.\n\n"
        "Что проверяю автоматически:\n"
        "- категория земель и ВРИ (можно ли построить дом)\n"
        "- площадь и кадастровая стоимость против цены продавца\n"
        "- цена за сотку против ориентира по району\n"
        "- «без документов», «по доверенности», «не межеван» и прочие маркеры\n\n"
        "Что придётся глянуть руками (дам прямые ссылки):\n"
        "- зона затопления/подтопления - при ней жильё строить запрещено\n"
        "- водоохранная зона и береговая полоса 20 м\n"
        "- наложение на земли лесного фонда\n"
        "- собственник и обременения - только выписка ЕГРН\n\n"
        "Команда: /uchastok 23:02:0209000:1629"
    ),
}

conversation_history: dict = {}
last_message_time: dict = {}
processing_users: set = set()
pdf_context: dict = {}


def get_main_keyboard():
    keyboard = [
        [KeyboardButton("🔥 СПС"), KeyboardButton("📢 СОУЭ")],
        [KeyboardButton("🔐 СКУД"), KeyboardButton("📹 CCTV")],
        [KeyboardButton("🔌 СКС"), KeyboardButton("⚡ Питание")],
        [KeyboardButton("📋 Документация"), KeyboardButton("🍺 Пивоварение")],
        [KeyboardButton("🥃 Дистилляция"), KeyboardButton("🌾 Рецепты браг")],
        [KeyboardButton("🍋 Ur-Weizen"), KeyboardButton("🌐 Новости")],
        [KeyboardButton("🏞 Участок")],
        [KeyboardButton("🔄 Сброс истории")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


async def send_typing_action(context, chat_id: int, stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        await asyncio.sleep(TYPING_INTERVAL)


async def search_news(query: str) -> str:
    if not SERPER_API_KEY:
        return ""
    try:
        url = "https://google.serper.dev/news"
        headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
        payload = {"q": query, "num": 5, "hl": "ru", "gl": "ru"}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        results = data.get("news", [])
        if not results:
            return ""
        lines = ["=== АКТУАЛЬНЫЕ НОВОСТИ ==="]
        for i, item in enumerate(results[:5], 1):
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            date = item.get("date", "")
            source = item.get("source", "")
            lines.append(f"{i}. [{date}] {source}: {title}. {snippet}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Ошибка поиска новостей: {e}")
        return ""


# ========== ПРОВЕРКА ЗЕМЕЛЬНЫХ УЧАСТКОВ ==========

CADASTRAL_RE = re.compile(r"\b(\d{2}:\d{2}:\d{6,7}:\d{1,7})\b")
PRICE_RE = re.compile(r"(\d[\d\s ]{4,})\s*(?:₽|руб|р\.)", re.IGNORECASE)
AREA_RE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:сот|соток|сотки|сотка)", re.IGNORECASE)

# Ориентиры рынка: тыс. руб за сотку, ЛПХ/ИЖС без коммуникаций
LAND_PRICE_REF = [
    (("северск", "убинск", "крепостн", "планческ", "ставропольск", "азовск", "дербентск"),
     "Северский р-н", 40, 90),
    (("горячий ключ", "фанагорийск", "безымянн", "кутаис", "пятигорск", "саратовск"),
     "Горячий Ключ", 25, 60),
    (("апшеронск", "черниговск", "нижегородск", "кубанск", "отдалённ", "отдаленн",
      "маратук", "терзиян", "хадыженск", "мезмай", "гуамк"),
     "Апшеронский р-н", 8, 35),
    (("мостовск", "псебай", "никитино", "баговск", "бесленеевск", "ходзь", "бурный"),
     "Мостовский р-н", 8, 30),
]

# Формулировки в объявлении, за которыми обычно стоит проблема
RISK_PHRASES = {
    "без документов": "документов нет - сделки не будет",
    "по доверенности": "продажа по доверенности - риск отзыва",
    "не межеван": "границы не установлены, нужен кадастровый инженер",
    "границы не установлены": "нужно межевание за свой счёт",
    "документы в процессе": "права ещё не зарегистрированы",
    "оформляем": "права ещё не зарегистрированы",
    "садовая книжка": "не собственность, а членство в СНТ",
    "членская книжка": "не собственность, а членство в СНТ",
    "сельхозназначен": "жилой дом на сельхозземле не зарегистрировать",
    "пай": "земельная доля, а не участок - выделять отдельно",
    "аренда": "аренда, а не собственность",
    "срочно": "давление на скорость - типовой приём",
    "торг": "цена изначально завышена",
}

# Ключи ответа ЕГРН у НСПД и ПКК называются по-разному - ищем по любому из списка
FIELD_ALIASES = {
    "address": ("readable_address", "address", "adress"),
    "area": ("area_value", "specified_area", "declared_area", "area"),
    "category": ("land_record_category_type", "category_type", "category"),
    "permitted_use": ("permitted_use_established_by_document", "util_by_doc",
                      "permitted_use_name", "utilization"),
    "cost": ("cost_value", "cad_cost", "cadastral_cost"),
    "status": ("status", "land_record_status", "statecd"),
    "reg_date": ("land_record_reg_date", "date_create", "cc_date_entering"),
}


def find_cadastral_numbers(text: str) -> list:
    """Вытаскивает кадастровые номера из произвольного текста объявления."""
    seen = []
    for cn in CADASTRAL_RE.findall(text):
        if cn not in seen:
            seen.append(cn)
    return seen


def parse_listing(text: str) -> dict:
    """Достаёт из текста объявления цену, площадь и опасные формулировки."""
    result = {"price": None, "area_sot": None, "price_per_sot": None, "risks": [],
              "text": text}

    prices = [int(re.sub(r"\D", "", p)) for p in PRICE_RE.findall(text) if re.sub(r"\D", "", p)]
    prices = [p for p in prices if 10_000 <= p <= 500_000_000]
    if prices:
        result["price"] = max(prices)

    areas = AREA_RE.findall(text)
    if areas:
        try:
            result["area_sot"] = float(areas[0].replace(",", "."))
        except ValueError:
            pass

    if result["price"] and result["area_sot"]:
        result["price_per_sot"] = result["price"] / result["area_sot"] / 1000

    lowered = text.lower()
    for phrase, why in RISK_PHRASES.items():
        if phrase in lowered:
            result["risks"].append(f"«{phrase}» - {why}")

    return result


def _pick_field(attrs: dict, key: str):
    """Достаёт поле по любому из известных псевдонимов, обходя вложенные словари."""
    aliases = FIELD_ALIASES[key]
    stack = [attrs]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        for alias in aliases:
            value = current.get(alias)
            if value not in (None, "", []):
                return value
        stack.extend(v for v in current.values() if isinstance(v, dict))
    return None


def mercator_to_wgs84(x: float, y: float) -> tuple:
    """EPSG:3857 -> широта/долгота. И НСПД, и ПКК отдают координаты в меркаторе."""
    lon = x / 20037508.34 * 180
    lat = y / 20037508.34 * 180
    lat = 180 / math.pi * (2 * math.atan(math.exp(lat * math.pi / 180)) - math.pi / 2)
    return lat, lon


def _center_from_geometry(feature: dict) -> tuple:
    """Центр участка: у ПКК готовый center, у НСПД - считаем по координатам геометрии."""
    center = feature.get("center")
    if isinstance(center, dict) and "x" in center:
        return float(center["x"]), float(center["y"])

    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates")
    flat = []

    def walk(node):
        if isinstance(node, (int, float)):
            flat.append(float(node))
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(coords)
    if len(flat) >= 2:
        xs = flat[0::2]
        ys = flat[1::2]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    return None, None


async def _fetch_nspd(client: httpx.AsyncClient, cn: str) -> dict:
    """Основной источник - НСПД (nspd.gov.ru), наследник ПКК."""
    url = "https://nspd.gov.ru/api/geoportal/v2/search/geoportal"
    resp = await client.get(url, params={"query": cn, "thematicSearchId": 1})
    resp.raise_for_status()
    data = resp.json()
    features = (data.get("data") or {}).get("features") or data.get("features") or []
    if not features:
        return {}
    feature = features[0]
    attrs = feature.get("properties") or feature.get("attrs") or {}
    x, y = _center_from_geometry(feature)
    return {"attrs": attrs, "x": x, "y": y, "source": "НСПД"}


async def _fetch_pkk(client: httpx.AsyncClient, cn: str) -> dict:
    """Резервный источник - старая ПКК Росреестра."""
    url = "https://pkk.rosreestr.ru/api/features/1"
    resp = await client.get(url, params={"text": cn, "tolerance": 1, "limit": 1})
    resp.raise_for_status()
    data = resp.json()
    features = data.get("features") or []
    if not features:
        return {}
    feature = features[0]
    x, y = _center_from_geometry(feature)
    return {"attrs": feature.get("attrs") or {}, "x": x, "y": y, "source": "ПКК Росреестра"}


async def lookup_cadastral(cn: str) -> dict:
    """Данные ЕГРН по кадастровому номеру. Пустой dict - ничего не нашли."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=KADASTR_TIMEOUT, headers=headers,
                                 verify=KADASTR_VERIFY_SSL, follow_redirects=True) as client:
        for fetch in (_fetch_nspd, _fetch_pkk):
            try:
                result = await fetch(client, cn)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"Кадастр {cn}: {fetch.__name__} не ответил: {e}")
    return {}


def _market_verdict(address: str, price_per_sot: float) -> str:
    """Сравнивает цену за сотку с ориентиром по району."""
    if not price_per_sot:
        return ""
    haystack = (address or "").lower()
    for keywords, name, low, high in LAND_PRICE_REF:
        if any(kw in haystack for kw in keywords):
            if price_per_sot > high:
                mark = "ВЫШЕ РЫНКА"
            elif price_per_sot < low:
                mark = "НИЖЕ РЫНКА - выяснить почему"
            else:
                mark = "в рынке"
            return f"Ориентир {name}: {low}-{high} тыс/сотка -> {mark}"
    return ""


def format_land_report(cn: str, data: dict, listing: dict) -> str:
    """Собирает готовый отчёт для Telegram."""
    lines = [f"🏞 УЧАСТОК {cn}", ""]

    address = ""
    if data:
        attrs = data["attrs"]
        address = str(_pick_field(attrs, "address") or "")
        area = _pick_field(attrs, "area")
        category = _pick_field(attrs, "category")
        permitted = _pick_field(attrs, "permitted_use")
        cost = _pick_field(attrs, "cost")
        status = _pick_field(attrs, "status")

        lines.append(f"Источник: {data['source']}")
        if address:
            lines.append(f"Адрес: {address}")
        if area:
            try:
                area_val = float(str(area).replace(",", "."))
                lines.append(f"Площадь: {area_val:.0f} м² ({area_val / 100:.1f} сот)")
            except ValueError:
                lines.append(f"Площадь: {area}")
        if category:
            lines.append(f"Категория: {category}")
        if permitted:
            lines.append(f"ВРИ: {permitted}")
        if cost:
            try:
                lines.append(f"Кадастровая стоимость: {float(cost):,.0f} ₽".replace(",", " "))
            except (ValueError, TypeError):
                lines.append(f"Кадастровая стоимость: {cost}")
        if status:
            lines.append(f"Статус: {status}")

        category_text = str(category or "").lower()
        permitted_text = str(permitted or "").lower()
        if "сельскохоз" in category_text:
            lines.append("")
            lines.append("❗ Земли сельхозназначения - жилой дом здесь не зарегистрировать.")
        if permitted_text and not any(
            k in permitted_text for k in ("жилищн", "жилой", "ижс", "личного подсобного", "лпх")
        ):
            lines.append("")
            lines.append(f"❗ ВРИ не под жильё: «{permitted}». Смена ВРИ - через ПЗЗ, "
                         "не факт что дадут.")
    else:
        lines.append("⚠️ ЕГРН не ответил или номер не найден.")
        lines.append("Если номер не находится и на карте вручную - участок снят с учёта "
                     "либо номер в объявлении чужой. Это стоп.")

    if listing.get("price"):
        lines.append("")
        lines.append("💰 ЦЕНА")
        lines.append(f"Запрос: {listing['price']:,} ₽".replace(",", " "))
        if listing.get("price_per_sot"):
            lines.append(f"За сотку: {listing['price_per_sot']:.1f} тыс ₽")
            # район ищем в адресе ЕГРН, а если его нет - в тексте объявления
            verdict = _market_verdict(address or listing.get("text", ""),
                                      listing["price_per_sot"])
            if verdict:
                lines.append(verdict)
        if data:
            cost = _pick_field(data["attrs"], "cost")
            try:
                ratio = listing["price"] / float(cost)
                lines.append(f"К кадастровой: x{ratio:.1f}")
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    if listing.get("risks"):
        lines.append("")
        lines.append("🚩 ФОРМУЛИРОВКИ В ОБЪЯВЛЕНИИ")
        lines.extend(f"- {r}" for r in listing["risks"])

    lines.append("")
    lines.append("🔗 ПРОВЕРИТЬ САМОМУ")
    if data and data.get("x") is not None:
        lat, lon = mercator_to_wgs84(data["x"], data["y"])
        lines.append(f"Координаты: {lat:.5f}, {lon:.5f}")
        lines.append(f"Спутник: https://yandex.ru/maps/?ll={lon:.5f}%2C{lat:.5f}&z=17&l=sat")
        lines.append(f"НСПД (слои ЗОУИТ): https://nspd.gov.ru/map?zoom=17"
                     f"&coordinate_x={lon:.5f}&coordinate_y={lat:.5f}")
    lines.append(f"ПКК: https://pkk.rosreestr.ru/#/search/{cn}")
    lines.append("Выписка ЕГРН: https://www.gosuslugi.ru/600133/1/form")

    lines.append("")
    lines.append("📋 ЧЕГО НЕТ В ОТКРЫТЫХ ДАННЫХ - СМОТРЕТЬ РУКАМИ")
    lines.append("1. Зона затопления/подтопления - слой ЗОУИТ на НСПД. "
                 "Есть зона = строить жильё запрещено (ст. 67.1 ВК РФ).")
    lines.append("2. Водоохранная зона и береговая полоса 20 м - в собственность не идёт.")
    lines.append("3. Наложение на земли лесного фонда - сверить границы.")
    lines.append("4. Собственник, обременения, аресты, срок владения - только выписка ЕГРН.")
    lines.append("5. Опора электросети ближе 300 м - иначе ТП от 300 тыс вместо 550 ₽.")
    lines.append("6. Съездить после дождя и спросить соседей, докуда доходила вода.")

    return "\n".join(lines)


async def check_land_plot(text: str) -> str:
    """Полный разбор: кадастровый номер + текст объявления."""
    numbers = find_cadastral_numbers(text)
    if not numbers:
        return ""
    listing = parse_listing(text)
    reports = []
    for cn in numbers[:3]:
        data = await lookup_cadastral(cn)
        reports.append(format_land_report(cn, data, listing))
    return "\n\n———\n\n".join(reports)


def extract_pdf_text(pdf_bytes: bytes) -> str:
    if FITZ_AVAILABLE:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages_text = []
            for page in doc:
                pages_text.append(page.get_text())
            doc.close()
            full_text = "\n".join(pages_text).strip()
            if len(full_text) > MAX_PDF_CHARS:
                full_text = full_text[:MAX_PDF_CHARS] + "\n\n[... текст обрезан ...]"
            return full_text
        except Exception as e:
            logger.error(f"fitz ошибка: {e}")
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages_text = []
        for page in reader.pages:
            pages_text.append(page.extract_text() or "")
        full_text = "\n".join(pages_text).strip()
        if len(full_text) > MAX_PDF_CHARS:
            full_text = full_text[:MAX_PDF_CHARS] + "\n\n[... текст обрезан ...]"
        return full_text
    except ImportError:
        pass
    except Exception as e:
        logger.error(f"pypdf ошибка: {e}")
    return ""


async def ask_groq(user_id: int, user_message: str, extra_context: str = "") -> str:
    if user_id not in conversation_history:
        conversation_history[user_id] = []

    pdf_text = pdf_context.get(user_id, "")

    # Определяем нужные модули по тексту пользователя + истории
    all_text = user_message + " ".join(
        m["content"] for m in conversation_history[user_id][-2:] if m["role"] == "user"
    )
    modules = select_modules(all_text)

    # Если модули не определились - добавляем базовый fire как fallback для слаботочки
    # (чтобы бот не был совсем пустым)
    system_prompt = build_system_prompt(modules, pdf_text, extra_context)

    conversation_history[user_id].append({"role": "user", "content": user_message})

    # Обрезаем историю
    if len(conversation_history[user_id]) > MAX_HISTORY:
        conversation_history[user_id] = conversation_history[user_id][-MAX_HISTORY:]

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}

    messages = [{"role": "system", "content": system_prompt}] + conversation_history[user_id]

    last_error = None

    for model in GROQ_MODELS:
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": MAX_TOKENS,
                    "temperature": 0.4
                }
                async with httpx.AsyncClient(timeout=40) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    answer = data["choices"][0]["message"]["content"]

                conversation_history[user_id].append({"role": "assistant", "content": answer})
                logger.info(f"OK model={model} user={user_id} modules={modules}")
                return answer

            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code
                logger.warning(f"HTTP {status} model={model} attempt={attempt}")
                if status in (401, 403):
                    raise Exception("неверный_ключ")
                if status == 429:
                    # Экспоненциальный backoff при rate limit
                    wait = RETRY_DELAY * (2 ** attempt)
                    logger.warning(f"Rate limit, жду {wait}с...")
                    await asyncio.sleep(wait)
                elif status == 503:
                    await asyncio.sleep(RETRY_DELAY * attempt)
                else:
                    await asyncio.sleep(RETRY_DELAY)

            except httpx.TimeoutException:
                last_error = Exception(f"Timeout model={model}")
                logger.warning(f"Таймаут model={model} attempt={attempt}")
                await asyncio.sleep(RETRY_DELAY)

            except Exception as e:
                last_error = e
                logger.warning(f"Ошибка model={model} attempt={attempt}: {e}")
                await asyncio.sleep(RETRY_DELAY)

        logger.warning(f"Модель {model} недоступна, следующая...")

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
    pdf_context.pop(user_id, None)
    logger.info(f"START: user={user_id} ({user_name})")
    await update.message.reply_text(
        f"👋 Привет, {user_name}!\n\n"
        "Я многопрофильный ИИ-ассистент. Умею:\n\n"
        "🏗 Слаботочные системы - СПС, СОУЭ, СКУД, CCTV, СКС (нормы 2025-2026)\n"
        "🍺 Пивоварение и дистилляция - рецептура, расчёты, технология\n"
        "🏞 Проверять земельные участки - кинь кадастровый номер или объявление с Авито\n"
        "📄 Читать PDF - пришли файл и задай вопрос по нему\n"
        "🌐 Искать свежие новости - кнопка Новости или спроси сам\n\n"
        "Выбери раздел или задай вопрос 👇",
        reply_markup=get_main_keyboard()
    )


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    pdf_context.pop(user_id, None)
    logger.info(f"RESET: user={user_id}")
    await update.message.reply_text("🔄 История и PDF-контекст очищены!", reply_markup=get_main_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Возможности бота:\n\n"
        "📄 PDF - пришли любой PDF-файл, бот прочитает и ответит на вопросы\n"
        "🌐 Новости - свежие новости по любой теме\n"
        "🔥 СПС, СОУЭ, СКУД, CCTV, СКС, Питание, Документация - слаботочные системы\n"
        "🍺 Пивоварение, 🥃 Дистилляция, 🌾 Рецепты - напитки профуровня\n"
        "🍋 Ur-Weizen - мой рецепт пшеничного пива\n"
        "🏞 Участок - проверка земли по ЕГРН: категория, ВРИ, цена за сотку, риски\n\n"
        "Примеры:\n"
        "- Рассчитай АКБ: ток 0.5А, резерв 24 часа\n"
        "- Рецепт IPA на 25 литров, 60 IBU\n"
        "- Последние новости пожарная безопасность\n"
        "- /uchastok 23:02:0209000:1629\n\n"
        "/reset - очистить историю и PDF",
        reply_markup=get_main_keyboard()
    )


async def land_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/uchastok 23:02:0209000:1629 - разбор участка по кадастровому номеру."""
    query = " ".join(context.args) if context.args else ""
    if not find_cadastral_numbers(query):
        await update.message.reply_text(
            "Дай кадастровый номер в формате 23:02:0209000:1629\n\n"
            "Пример: /uchastok 23:02:0209000:1629\n"
            "Или просто пришли текст объявления - номер найду сам.",
            reply_markup=get_main_keyboard()
        )
        return
    await run_land_check(update, context, query)


async def run_land_check(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Общий прогон проверки участка - и для команды, и для обычного сообщения."""
    user_id = update.effective_user.id
    if user_id in processing_users:
        await update.message.reply_text("⏳ Подожди, обрабатываю предыдущий запрос...")
        return
    processing_users.add(user_id)
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        send_typing_action(context, update.effective_chat.id, stop_typing)
    )
    try:
        await update.message.reply_text("🏞 Нашёл кадастровый номер, поднимаю ЕГРН...")
        report = await check_land_plot(text)
        stop_typing.set()
        await typing_task
        if not report:
            await update.message.reply_text(
                "❌ Не смог разобрать номер. Проверь формат: 23:02:0209000:1629",
                reply_markup=get_main_keyboard()
            )
            return
        await send_long_message(update, report)
        logger.info(f"LAND check: user={user_id}, {find_cadastral_numbers(text)}")
    except Exception as e:
        stop_typing.set()
        await typing_task
        logger.error(f"LAND error user={user_id}: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Росреестр не ответил. Проверь вручную: https://nspd.gov.ru/map",
            reply_markup=get_main_keyboard()
        )
    finally:
        processing_users.discard(user_id)


async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in processing_users:
        await update.message.reply_text("⏳ Подожди, обрабатываю предыдущий запрос...")
        return
    processing_users.add(user_id)
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        send_typing_action(context, update.effective_chat.id, stop_typing)
    )
    try:
        await update.message.reply_text("📄 Получил PDF, читаю...")
        doc = update.message.document
        file = await context.bot.get_file(doc.file_id)
        pdf_bytes = await file.download_as_bytearray()
        text = extract_pdf_text(bytes(pdf_bytes))
        if not text or len(text.strip()) < 50:
            stop_typing.set()
            await typing_task
            await update.message.reply_text(
                "❌ Не удалось извлечь текст из PDF. Возможно, это сканированный документ. "
                "Попробуй PDF с текстовым слоем.",
                reply_markup=get_main_keyboard()
            )
            return
        pdf_context[user_id] = text
        stop_typing.set()
        await typing_task
        await update.message.reply_text(
            f"✅ PDF прочитан (~{len(text)} символов).\n\n"
            "Задавай вопросы по содержимому!\n"
            "🔄 Сброс истории - убрать PDF из контекста.",
            reply_markup=get_main_keyboard()
        )
        logger.info(f"PDF loaded: user={user_id}, chars={len(text)}")
    except Exception as e:
        stop_typing.set()
        await typing_task
        logger.error(f"PDF error user={user_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при чтении PDF.", reply_markup=get_main_keyboard())
    finally:
        processing_users.discard(user_id)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text.strip()

    if user_text == "🔄 Сброс истории":
        await reset_cmd(update, context)
        return

    if user_text in SECTION_TEXTS:
        await update.message.reply_text(SECTION_TEXTS[user_text], reply_markup=get_main_keyboard())
        return

    if user_text == "🌐 Новости":
        await update.message.reply_text(
            "🌐 По какой теме ищем новости?\n\n"
            "Напиши тему, например:\n"
            "- пожарная безопасность\n"
            "- пивоварение\n"
            "- слаботочные системы\n"
            "- строительные нормы 2026",
            reply_markup=get_main_keyboard()
        )
        return

    # Кадастровый номер в тексте - это всегда запрос на проверку участка,
    # к нейросети такое отправлять бессмысленно
    if find_cadastral_numbers(user_text):
        await run_land_check(update, context, user_text)
        return

    if user_id in processing_users:
        await update.message.reply_text("⏳ Обрабатываю предыдущий вопрос, подожди...")
        return

    now = time.time()
    if user_id in last_message_time and (now - last_message_time[user_id]) < RATE_LIMIT_SECONDS:
        return
    last_message_time[user_id] = now

    processing_users.add(user_id)
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        send_typing_action(context, update.effective_chat.id, stop_typing)
    )

    try:
        logger.info(f"MSG user={user_id}: {user_text[:80]}")

        news_keywords = ["новост", "последние", "свежи", "сегодня", "2025", "2026",
                         "актуальн", "что нового", "что произошло"]
        need_news = any(kw in user_text.lower() for kw in news_keywords)

        extra_context = ""
        if need_news and SERPER_API_KEY:
            extra_context = await search_news(user_text)

        answer = await ask_groq(user_id, user_text, extra_context)
        stop_typing.set()
        await typing_task
        await send_long_message(update, answer)

    except Exception as e:
        stop_typing.set()
        await typing_task
        err = str(e)
        logger.error(f"ERR user={user_id}: {err}", exc_info=True)
        if "неверный_ключ" in err:
            msg = "❌ Проблема с API ключом. Обратитесь к администратору."
        elif "groq_недоступен" in err:
            msg = "⏱ Сервис временно перегружен. Попробуй через минуту."
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
    app.add_handler(CommandHandler("uchastok", land_cmd))
    app.add_handler(CommandHandler("kadastr", land_cmd))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message"])
