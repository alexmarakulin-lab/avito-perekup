# -*- coding: utf-8 -*-
"""Обновление бота на домашнем компьютере: свежий код, библиотеки, проверки."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
PY = os.path.join(HERE, "venv", "Scripts", "python.exe")
RULER = "=" * 62


def step(title, args, executable=None):
    print(f"\n{RULER}\n  {title}\n{RULER}")
    try:
        return subprocess.call(args, executable=executable)
    except FileNotFoundError:
        print(f"  Не нашёл программу: {args[0]}")
        return 1


if step("Забираю свежий код с GitHub", ["git", "pull"]) != 0:
    print("\n  Не получилось. Обычно помогает повторный запуск: связь рваная.")

if not os.path.exists(PY):
    step("Создаю рабочее окружение, это пара минут", [sys.executable, "-m", "venv", "venv"])

step("Доставляю библиотеки", [PY, "-m", "pip", "install", "-q",
                              "--disable-pip-version-check", "-r", "requirements.txt"])

# Сам браузер идёт отдельно от библиотеки: это ещё около 150 МБ. Скачивается
# один раз, при повторных запусках команда просто убеждается, что он на месте.
step("Проверяю браузер для Авито", [PY, "-m", "playwright", "install", "chromium"])

# Настройки, появившиеся в новых версиях, сами в .env не приходят: файл
# создаётся один раз копией примера и дальше живёт своей жизнью. Чужие
# значения при этом не трогаются - только добавляются недостающие.
step("Обновляю список настроек", [PY, "env_merge.py"])

# Проверки запускаются с перехватом вывода - не чтобы его прятать, а чтобы
# в конце повторить упавшее.
#
# Раньше окно писало «Проверки не прошли (1 из 3)» и не говорило, какие
# именно и в каком файле. Всё это было на экране, но выше на сотню строк, и
# разбор начинался с просьбы прокрутить наверх. Теперь виноватое собирается
# в конец, туда, куда человек и смотрит.
TESTS = ("test_avito_monitor.py", "test_avito_bot.py", "test_resale_expert.py")


def read(raw: bytes) -> str:
    """Раскодировать вывод, не рассчитывая на кодировку системы.

    На русской Windows она cp1251, и один символ вне её вываливает
    Traceback из читающего потока subprocess - там, где его не ловит
    никакой try. На этом уже обжигались, поэтому байты и перебор.
    """
    for encoding in ("utf-8", "cp866", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


broken = []
for name in TESTS:
    print(f"\n{RULER}\n  Проверяю: {name}\n{RULER}")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        run = subprocess.run([PY, name], capture_output=True, env=env, timeout=600)
    except FileNotFoundError:
        print(f"  Не нашёл программу: {PY}")
        broken.append((name, ["не запустилось: нет рабочего окружения"]))
        continue
    except subprocess.TimeoutExpired:
        print("  Проверки не уложились в 10 минут.")
        broken.append((name, ["зависло"]))
        continue

    out = read(run.stdout) + read(run.stderr)
    print(out)
    if run.returncode != 0:
        # Забираем строки провалов и, если их нет, последние строки вывода:
        # значит упало не на проверке, а раньше - на ошибке в самом файле.
        lines = [l.rstrip() for l in out.splitlines()
                 if l.startswith(" FAIL") or l.startswith("ПРОВАЛЕНО")]
        broken.append((name, lines or out.strip().splitlines()[-12:]))

print("\n" + RULER)
if broken:
    print(f"  Проверки не прошли: {len(broken)} из {len(TESTS)}.")
    for name, lines in broken:
        print(f"\n  {name}")
        for line in lines:
            print("    " + line)
    print("")
    print("  Покажи это место - по нему всё видно, наверх листать не нужно.")
else:
    print("  Всё цело. Можно запускать бота.")
print(RULER)
