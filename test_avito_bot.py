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

    # Свободный текст уходит консультанту. Ключа Groq в тестах нет,
    # значит должен прийти внятный отказ, а не молчание или трейсбек.
    import resale_expert
    resale_expert._last_call.clear()
    unknown = FakeUpdate("перфоратор за 3500, брать?", 777)
    await avito_bot.on_button(unknown, FakeContext())
    joined = " ".join(unknown.message.replies)
    check("свободный текст: уходит консультанту", bool(unknown.message.replies), joined)
    check("свободный текст: без ключа честно сообщает", "GROQ_API_KEY" in joined, joined[:90])

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
                       "help", "myid", "reset", "start"], commands)
    check("сборка: обработчик кнопок добавлен", len(handlers) == len(commands) + 1, len(handlers))
    check("сборка: post_init назначен", callable(app.post_init))

    # Сторож бесполезен, если приставлен не к тому соединению: библиотека
    # держит опрос и всё остальное на разных наборах, и прежний сторож
    # сторожил не тот. Проверка прямая - опрос идёт через наблюдаемый класс.
    try:
        watched = isinstance(app.bot._request[0], avito_bot.WatchedRequest)
    except Exception as exc:
        watched = f"не добрался до соединения опроса: {exc}"
    check("сборка: сторож приставлен именно к опросу", watched is True, watched)

    async def post_init_runs():
        started = {"v": False}

        def fake_create_task(self, coro, **kw):
            started["v"] = True
            coro.close()   # корутину надо закрыть, иначе Python ругнётся

        # Подменяем на самом классе, а не на объекте: с версии 22 у него
        # запрещено дописывать свойства на ходу.
        from telegram.ext import Application
        original = Application.create_task
        Application.create_task = fake_create_task
        try:
            await app.post_init(app)
        finally:
            Application.create_task = original
        check("сборка: фоновый монитор стартует", started["v"])

    asyncio.run(post_init_runs())
else:
    print("  ~~  python-telegram-bot не установлен, проверки сборки пропущены")

# --- запуск без токена ---
env = dict(os.environ)
env.pop("AVITO_BOT_TOKEN", None)
env["AVITO_BOT_TOKEN"] = ""
proc = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "avito_bot.py")],
                      capture_output=True, text=True, env=env, timeout=60)
check("без токена: выход с ошибкой", proc.returncode == 1, proc.returncode)
check("без токена: подсказка про BotFather", "BotFather" in proc.stderr, proc.stderr[:120])

print("\n" + ("ВСЁ ЗЕЛЁНОЕ" if not fails else f"ПРОВАЛЕНО: {fails}"))
try:
    os.path.exists(os.environ["AVITO_DB"]) and os.unlink(os.environ["AVITO_DB"])
except OSError:
    pass   # на Windows файл базы остаётся занятым, это не провал проверок
sys.exit(1 if fails else 0)
