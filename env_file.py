# -*- coding: utf-8 -*-
"""Чтение настроек из .env в переменные окружения.

На сервере это делал Docker: он сам подставлял значения из .env в контейнер.
При запуске на своём компьютере Docker'а нет, и читать файл некому - бот не
увидел бы ни токена, ни номера хозяина и молча вёл бы себя как ненастроенный.
"""
import os


def load(path: str = None) -> int:
    """Вписывает значения из .env в окружение. Возвращает, сколько прочитал.

    Уже заданные переменные не трогает: то, что задано снаружи (например
    docker-compose), главнее файла.
    """
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return 0

    count = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            # Кавычки вокруг значения - частая привычка из других файлов
            # настроек. Здесь они не нужны и попали бы внутрь пароля.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
                count += 1
    return count
