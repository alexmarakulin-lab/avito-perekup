# -*- coding: utf-8 -*-
"""Подтягивает в .env настройки, появившиеся в новых версиях.

Зачем это нужно. Файл `.env` создаётся один раз копией `.env.example` и
дальше живёт своей жизнью. Всё, что добавляется в пример потом - канал,
дни недельных постов, пороги, - до работающего бота не доходит никогда.
Работать он продолжает: неизвестные настройки берутся из умолчаний в коде.
Но увидеть их и покрутить владелец не может, а значит их всё равно что нет.

Два правила, и оба важны:

1. **Чужие значения не трогаем.** Если настройка в `.env` уже есть, она
   остаётся как была, даже если в примере теперь другое число. Владелец мог
   поставить своё, и молча его переписать - худшее, что может сделать
   обновление.

2. **Про изменения говорим вслух.** Всё, что добавлено или поднято,
   печатается списком. Обновление, которое меняет поведение бота
   молча, - это то же самое, что поломка: разбираться потом придётся с
   тем же удивлением.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, ".env")
SAMPLE = os.path.join(HERE, ".env.example")

# Настройки, которые можно поднять до нового умолчания, - но только если в
# `.env` они стоят ровно в прежнем умолчании, то есть их никто не трогал.
#
# Список нарочно короткий и именной. Соблазн «обновлять всё, что совпадает с
# прежним значением» надо давить: совпасть с умолчанием можно и намеренно,
# и тогда обновление отменит осознанный выбор.
UPGRADES = {
    # 27.08.2026: обход всех слов занимал больше пяти часов. Для лота вдвое
    # дешевле рынка это долго - его успевают забрать.
    "AVITO_QUERIES_PER_CYCLE": ("3", "5"),
    "AVITO_CYCLE_PAUSE": ("900", "600"),
}

KEY = re.compile(r"^\s*([A-Z_0-9]+)\s*=(.*)$")


def parse(text: str) -> dict:
    """Настройки из текста файла: имя -> значение, как записано."""
    out = {}
    for line in text.splitlines():
        m = KEY.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def sample_blocks(text: str) -> list:
    """Разбирает пример на куски «комментарии + настройка».

    Комментарии переносятся вместе с настройкой не для красоты: в них
    записано, почему значение такое, а не другое. Настройка без объяснения
    через полгода выглядит случайной, и первое, что с ней делают, - крутят
    наугад.
    """
    blocks, buf = [], []
    for line in text.splitlines():
        m = KEY.match(line)
        if m:
            blocks.append((m.group(1), buf + [line]))
            buf = []
        elif line.strip().startswith("#"):
            buf.append(line)
        else:
            buf = []                      # пустая строка обрывает комментарий
    return blocks


def merge(env_text: str, sample_text: str) -> tuple:
    """Возвращает (новый текст .env, что добавлено, что поднято)."""
    have = parse(env_text)
    added, raised = [], []

    for key, (was, now) in UPGRADES.items():
        if key in have and have[key] == was:
            env_text = re.sub(rf"^\s*{key}\s*=.*$", f"{key}={now}",
                              env_text, count=1, flags=re.M)
            raised.append((key, was, now))

    tail = []
    for key, lines in sample_blocks(sample_text):
        if key in have:
            continue
        tail.extend([""] + lines)
        added.append(key)

    if tail:
        env_text = env_text.rstrip("\n") + "\n" + "\n".join(tail).lstrip("\n") + "\n"
    return env_text, added, raised


def main() -> int:
    if not os.path.exists(ENV) or not os.path.exists(SAMPLE):
        return 0

    with open(ENV, encoding="utf-8") as f:
        env_text = f.read()
    with open(SAMPLE, encoding="utf-8") as f:
        sample_text = f.read()

    new_text, added, raised = merge(env_text, sample_text)
    if new_text == env_text:
        print("  Настройки уже полные, менять нечего.")
        return 0

    with open(ENV, "w", encoding="utf-8") as f:
        f.write(new_text)

    for key, was, now in raised:
        print(f"  Поднято: {key} было {was}, стало {now}")
    if added:
        print(f"  Добавлено новых настроек: {len(added)}")
        for key in added:
            print(f"    {key}")
        print("  Значения умолчательные, менять не обязательно.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
