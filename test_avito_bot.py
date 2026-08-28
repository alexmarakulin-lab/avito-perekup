# -*- coding: utf-8 -*-
"""Оффлайн-проверка бота-перекупа: доступ, кнопки, обвязка. Сеть не нужна."""
import asyncio
import os
import subprocess
import sys
import tempfile

os.environ["AVITO_DB"] = tempfile.mktemp(suffix=".db")
os.environ["AVITO_BOT_TOKEN"] = "123456:AAFAKE_TOKEN_FOR_TESTS"
os.environ["AVITO_OWNER_ID"] = "777, 888"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import avito_bot
import avito_monitor

fails = []


def check(name, cond, extra=""):
    print(("  OK  " if cond else " FAIL ") + name + (f"  <- {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeUpdate:
    def __init__(self, text, uid):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(uid)
        self.effective_chat = type("C", (), {"id": uid})()


class FakeContext:
    args = []


# --- разбор списка владельцев ---
check("владельцы: разобраны из строки с пробелами", avito_bot.OWNER_IDS == {777, 888},
      avito_bot.OWNER_IDS)

# --- контроль доступа ---
async def access():
    stranger = FakeUpdate("/avito", 123)
    check("доступ: чужой не проходит", not await avito_bot.guard(stranger))
    check("доступ: чужому показан его ID", "123" in stranger.message.replies[0],
          stranger.message.replies)

    owner = FakeUpdate("/avito", 777)
    check("доступ: владелец проходит", await avito_bot.guard(owner))
    check("доступ: владельцу ничего не отвечаем", owner.message.replies == [])

    saved = avito_bot.OWNER_IDS
    avito_bot.OWNER_IDS = set()
    check("доступ: пустой список пускает всех", await avito_bot.guard(FakeUpdate("x", 999)))
    avito_bot.OWNER_IDS = saved

asyncio.run(access())

# --- кнопки ---
avito_monitor.init_db()


async def buttons():
    for label, marker in [
        (avito_bot.BTN_STATUS, "Монитор"),
        (avito_bot.BTN_REPORT, "Отчёт по рынку"),
        (avito_bot.BTN_ON, "включ"),
        (avito_bot.BTN_OFF, "выключен"),
    ]:
        upd = FakeUpdate(label, 777)
        await avito_bot.on_button(upd, FakeContext())
        joined = " ".join(upd.message.replies)
        check(f"кнопка «{label}» отвечает по делу", marker.lower() in joined.lower(),
              joined[:90])

    check("состояние после «Выключить» — монитор остановлен",
          avito_monitor.is_enabled() is False)

    # Свободный текст уходит консультанту. Проверяем отказ без ключа: должно
    # прийти внятное объяснение, а не молчание или трейсбек.
    #
    # Ключ убирается принудительно. Раньше проверка полагалась на то, что в
    # окружении его просто нет, - и развалилась в тот день, когда ключ
    # наконец вписали в .env: бот его подхватывает при импорте. Проверка,
    # зависящая от чужих настроек, рано или поздно врёт, а эта вдобавок
    # полезла бы в сеть.
    import resale_expert
    resale_expert._last_call.clear()
    saved_key, resale_expert.GROQ_API_KEY = resale_expert.GROQ_API_KEY, ""
    try:
        unknown = FakeUpdate("перфоратор за 3500, брать?", 777)
        await avito_bot.on_button(unknown, FakeContext())
        joined = " ".join(unknown.message.replies)
        check("свободный текст: уходит консультанту", bool(unknown.message.replies), joined)
        check("свободный текст: без ключа честно сообщает",
              "GROQ_API_KEY" in joined, joined[:90])
    finally:
        resale_expert.GROQ_API_KEY = saved_key

    unknown2 = FakeUpdate("а стиралка?", 777)
    await avito_bot.on_button(unknown2, FakeContext())
    check("свободный текст: частые вопросы притормаживаются",
          "Не так быстро" in " ".join(unknown2.message.replies),
          unknown2.message.replies)

    stranger = FakeUpdate(avito_bot.BTN_REPORT, 555)
    await avito_bot.on_button(stranger, FakeContext())
    check("кнопки закрыты для чужих", "личный бот" in " ".join(stranger.message.replies).lower(),
          stranger.message.replies)

asyncio.run(buttons())

# --- порядок включения/выключения ---
async def toggle():
    upd_on = FakeUpdate(avito_bot.BTN_ON, 777)
    await avito_bot.on_button(upd_on, FakeContext())
    check("включение: монитор поднят", avito_monitor.is_enabled())
    check("включение: чат владельца записан", avito_monitor.get_owner_chat() == 777,
          avito_monitor.get_owner_chat())
    upd_off = FakeUpdate(avito_bot.BTN_OFF, 777)
    await avito_bot.on_button(upd_off, FakeContext())
    check("выключение: монитор остановлен", not avito_monitor.is_enabled())

asyncio.run(toggle())

# --- сборка приложения ---
try:
    from telegram.ext import CommandHandler  # noqa: F401
    PTB = True
except ImportError:
    PTB = False

if PTB:
    app = avito_bot.build_app()
    handlers = [h for hs in app.handlers.values() for h in hs]
    commands = sorted(sorted(h.commands)[0] for h in handlers if hasattr(h, "commands"))
    check("сборка: команды на месте",
          commands == ["avito", "avito_off", "avito_on", "avito_report", "avito_test",
                       "channel", "help", "myid", "preview", "reset", "selfcheck",
                       "start"], commands)
    check("сборка: обработчик кнопок добавлен", len(handlers) == len(commands) + 2, len(handlers))
    check("сборка: post_init назначен", callable(app.post_init))

    # Кнопки под сообщением приходят отдельным видом события. Не запросив
    # его у Telegram, бот их даже не получит - без ошибки, без записи в
    # логах, просто нажатие в пустоту. Проверка прямая, потому что заметить
    # такое можно только по молчанию.
    from telegram.ext import CallbackQueryHandler
    check("сборка: нажатия кнопок обрабатываются",
          any(isinstance(h, CallbackQueryHandler) for h in handlers))
    check("сборка: нажатия кнопок запрошены у Telegram",
          "callback_query" in avito_bot.ALLOWED_UPDATES, avito_bot.ALLOWED_UPDATES)

    # Опрос Telegram заводится ровно в одном месте - в avito_bot.py. Раньше
    # его заводил и домашний запускатель, и списки событий у них могли
    # разъехаться: починил бы один, а на живом боте вылезло бы из другого.
    # Теперь дома бот запускается отдельным процессом, и это место одно.
    run_bot_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "run_bot.py"), encoding="utf-8").read()
    check("сборка: домашний запуск не заводит опрос сам, а зовёт бота",
          "run_polling" not in run_bot_src, "run_bot.py снова опрашивает Telegram сам")

    # Сторож связи выходит из процесса, рассчитывая, что его поднимут
    # снаружи. На сервере это делает Docker, дома - некому, и выход означал
    # смерть насовсем. Так и случилось в ночь на 27.08.2026: компьютер
    # уснул, утром сторож увидел 12 часов молчания, вышел - и бот не
    # работал до вечера. Поэтому дома бот запускается отдельным процессом,
    # за которым присматривают.
    check("присмотр: бота поднимают заново, а не запускают в лоб",
          "subprocess.call" in run_bot_src and "avito_bot.py" in run_bot_src,
          "run_bot.py запускает бота напрямую - после выхода сторожа он не встанет")
    check("присмотр: есть предел на быстрые падения",
          "FAST_FAIL_LIMIT" in run_bot_src)

    # Сторож бесполезен, если приставлен не к тому соединению: библиотека
    # держит опрос и всё остальное на разных наборах, и прежний сторож
    # сторожил не тот. Проверка прямая - опрос идёт через наблюдаемый класс.
    try:
        watched = isinstance(app.bot._request[0], avito_bot.WatchedRequest)
    except Exception as exc:
        watched = f"не добрался до соединения опроса: {exc}"
    check("сборка: сторож приставлен именно к опросу", watched is True, watched)

    async def background_tasks_live_and_die():
        """Фоновые задачи должны стартовать и, главное, честно умирать.

        На отмене монитора висит закрытие браузера. Уйти, не дождавшись
        отмены, - значит оставить Chromium висеть чужим процессом до
        перезагрузки; дома, где бот поднимается после каждого обрыва связи,
        такие процессы копились бы за день десятками.
        """
        before = len(asyncio.all_tasks())
        await app.post_init(app)
        await asyncio.sleep(0)
        running = [t for t in asyncio.all_tasks() if not t.done()]
        check("сборка: фоновые задачи запущены", len(running) >= before + 2,
              f"{len(running)} против {before}")

        await app.post_shutdown(app)
        check("сборка: при выходе фоновые задачи отменены, а не брошены",
              len(asyncio.all_tasks()) <= before + 1, len(asyncio.all_tasks()))

    asyncio.run(background_tasks_live_and_die())
else:
    print("  ~~  python-telegram-bot не установлен, проверки сборки пропущены")

# --- запуск без токена ---
env = dict(os.environ)
env.pop("AVITO_BOT_TOKEN", None)
env["AVITO_BOT_TOKEN"] = ""
# Вывод забирается байтами, а не с `text=True`. С ним Python читает ответ в
# кодировке системы: на русской Windows это cp1251, и один символ вне её
# роняет читающий поток subprocess - там, где его не ловит никакой try. На
# этом уже обожглись с powercfg, второй раз наступать незачем.
env["PYTHONIOENCODING"] = "utf-8"
proc = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "avito_bot.py")],
                      capture_output=True, env=env, timeout=60)
stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
check("без токена: выход с ошибкой", proc.returncode == 1, proc.returncode)
check("без токена: подсказка про BotFather", "BotFather" in stderr, stderr[:120])
check("без токена: подсказка по-русски, а не крокозябрами",
      "Не задан" in stderr, stderr[:120])

# --- пустое значение в .env означает «не задано» ---
# Поломка 28.08.2026, стоившая владельцу капчи на первом же запросе.
# В примере половина настроек стоит пустыми, и все они значат «оставь как
# есть». Но код читает их как os.getenv("AVITO_FETCH", "browser"), а
# умолчание берётся только когда переменной нет вовсе: пустая строка его
# отменяет. Пока этих строк не было в .env, всё работало; как только
# обновление их дописало - браузер выключился, и бот пошёл по http прямо
# в капчу.
import env_file

env_path = tempfile.mktemp(suffix=".env")
with open(env_path, "w", encoding="utf-8") as f:
    f.write("AVITO_FETCH=\n"
            "AVITO_BROWSER_CHANNEL=\n"
            "AVITO_CHANNEL_CHAT=\n"
            "PROVERKA_ZNACHENIE=есть\n"
            "PROVERKA_PUSTO=\n")
for name in ("PROVERKA_ZNACHENIE", "PROVERKA_PUSTO", "AVITO_FETCH_PROBA"):
    os.environ.pop(name, None)
env_file.load(env_path)

check("настройки: значение из .env прочитано",
      os.environ.get("PROVERKA_ZNACHENIE") == "есть", os.environ.get("PROVERKA_ZNACHENIE"))
check("настройки: пустая строка не выставляется в окружение",
      "PROVERKA_PUSTO" not in os.environ, os.environ.get("PROVERKA_PUSTO"))
check("настройки: после пустой строки умолчание всё ещё работает",
      os.getenv("PROVERKA_PUSTO", "умолчание") == "умолчание")

# И то же самое на настоящих настройках, из-за которых всё и случилось.
import importlib
import avito_browser
importlib.reload(avito_browser)
check("настройки: пустой AVITO_FETCH не выключает браузер",
      (os.getenv("AVITO_FETCH") or "browser") == "browser", os.getenv("AVITO_FETCH"))
check("настройки: пустой AVITO_BROWSER_CHANNEL оставляет настоящий Chrome",
      avito_browser.CHANNEL == "chrome", avito_browser.CHANNEL)

os.environ["AVITO_BROWSER_CHANNEL"] = "chromium"
importlib.reload(avito_browser)
check("настройки: встроенный Chromium выбирается словом, а не пустотой",
      avito_browser.CHANNEL is None, avito_browser.CHANNEL)
os.environ["AVITO_BROWSER_CHANNEL"] = ""
importlib.reload(avito_browser)

try:
    os.unlink(env_path)
except OSError:
    pass

# --- перенос настроек в .env при обновлении ---
# Настройки, появившиеся в новых версиях, сами в .env не приходят: файл
# создаётся один раз копией примера и дальше живёт своей жизнью. Самое
# опасное здесь - переписать чужое значение, поэтому проверки в основном
# про то, чего трогать нельзя.
import env_merge

old_env = ("AVITO_BOT_TOKEN=123:SECRET\n"
           "AVITO_OWNER_ID=555\n"
           "AVITO_QUERIES_PER_CYCLE=3\n"
           "AVITO_CYCLE_PAUSE=900\n"
           "AVITO_REQ_DELAY_MIN=60\n")
sample = ("# токен от BotFather\n"
          "AVITO_BOT_TOKEN=\n"
          "AVITO_QUERIES_PER_CYCLE=5\n"
          "AVITO_CYCLE_PAUSE=600\n"
          "\n"
          "# Куда выкладывать находки.\n"
          "# Пусто - канала нет.\n"
          "AVITO_CHANNEL_CHAT=\n"
          "AVITO_CHANNEL_DELAY=1800\n")

merged, added, raised = env_merge.merge(old_env, sample)
vals = env_merge.parse(merged)

check("настройки: токен не тронут", vals["AVITO_BOT_TOKEN"] == "123:SECRET", vals)
check("настройки: своё значение не переписано", vals["AVITO_REQ_DELAY_MIN"] == "60", vals)
check("настройки: нетронутое умолчание поднято",
      vals["AVITO_QUERIES_PER_CYCLE"] == "5" and vals["AVITO_CYCLE_PAUSE"] == "600", vals)
check("настройки: про поднятое сказано вслух", len(raised) == 2, raised)
check("настройки: новые добавлены",
      vals["AVITO_CHANNEL_CHAT"] == "" and vals["AVITO_CHANNEL_DELAY"] == "1800", vals)
check("настройки: новые пришли с пояснением",
      "Пусто - канала нет." in merged, merged[-200:])
check("настройки: имена добавленного перечислены",
      set(added) == {"AVITO_CHANNEL_CHAT", "AVITO_CHANNEL_DELAY"}, added)

# Осознанно выставленное значение, случайно совпавшее с новым умолчанием,
# трогать тем более нельзя - а заодно и второй прогон не должен ничего менять.
again, added2, raised2 = env_merge.merge(merged, sample)
check("настройки: второй прогон ничего не меняет",
      again == merged and not added2 and not raised2, (added2, raised2))

# Владелец нарочно замедлил обход - обновление обязано это уважать.
custom = "AVITO_QUERIES_PER_CYCLE=2\nAVITO_CYCLE_PAUSE=1800\n"
kept, _, raised3 = env_merge.merge(custom, sample)
check("настройки: осознанно выбранное не поднимается",
      env_merge.parse(kept)["AVITO_QUERIES_PER_CYCLE"] == "2" and not raised3, kept)

print("\n" + ("ВСЁ ЗЕЛЁНОЕ" if not fails else f"ПРОВАЛЕНО: {fails}"))
try:
    os.path.exists(os.environ["AVITO_DB"]) and os.unlink(os.environ["AVITO_DB"])
except OSError:
    pass   # на Windows файл базы остаётся занятым, это не провал проверок
sys.exit(1 if fails else 0)
