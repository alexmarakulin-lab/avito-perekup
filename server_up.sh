#!/usr/bin/env bash
# Поднимает бота на сервере: проверяет машину, собирает образ, пробует
# Авито и только потом запускает.
#
# Почему одним скриптом, а не списком команд. Доступ на сервер только
# через VNC, а его буфер обмена режет длинные строки: команда длиннее
# полутора строк приезжает обрубком и выполняется наполовину. Одна короткая
# команда вместо десяти длинных - это не удобство, а способ не сломать.
#
# Запуск:   bash server_up.sh
set -u

RULER="=============================================================="
say() { echo; echo "$RULER"; echo "  $*"; echo "$RULER"; }

cd "$(dirname "$0")" || exit 1

# ---------- 1. Хватит ли машине сил ----------
say "1/5  Смотрю, потянет ли сервер"

RAM_MB=$(free -m | awk '/^Mem:/ {print $2}')
SWAP_MB=$(free -m | awk '/^Swap:/ {print $2}')
DISK_GB=$(df -BG --output=avail . | tail -1 | tr -dc '0-9')

echo "  память: ${RAM_MB} МБ, подкачка: ${SWAP_MB} МБ, свободно на диске: ${DISK_GB} ГБ"

if [ "${DISK_GB:-0}" -lt 3 ]; then
    echo
    echo "  МАЛО МЕСТА. Образ с браузером весит около 1,5 ГБ, нужно хотя бы 3 ГБ."
    echo "  Освободить: docker system prune -a"
    exit 1
fi

# Chromium в гигабайт влезает впритык. Файл подкачки - это место на диске,
# которое система использует как запасную память: медленно, но лучше, чем
# падение посреди работы.
if [ "${RAM_MB:-0}" -lt 2000 ] && [ "${SWAP_MB:-0}" -lt 512 ]; then
    say "Добавляю файл подкачки 2 ГБ - памяти в обрез"
    fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "  готово, подкачка включена и переживёт перезагрузку"
fi

# ---------- 2. Настройки ----------
say "2/5  Проверяю настройки"

if [ ! -f .env ]; then
    echo "  Нет файла .env. Скопируй образец и заполни:"
    echo "     cp .env.example .env"
    echo "     nano .env"
    exit 1
fi

miss=0
for key in AVITO_BOT_TOKEN AVITO_OWNER_ID; do
    if ! grep -q "^${key}=." .env; then
        echo "  не заполнено: ${key}"
        miss=1
    fi
done
[ "$miss" = 1 ] && { echo; echo "  Поправь: nano .env"; exit 1; }
echo "  токен и номер владельца на месте"

# ---------- 3. Сборка ----------
say "3/5  Собираю образ. Это 5-15 минут, качается браузер"

if ! docker compose build; then
    echo
    echo "  Сборка не прошла. Частая причина - недоступное зеркало образов."
    echo "  Тогда в Dockerfile поменяй первую строку на:"
    echo "     FROM python:3.11-slim"
    exit 1
fi

# ---------- 4. Проверка Авито ----------
say "4/5  Пробую прочитать Авито с этого сервера"
echo "  Это главная проверка. 6 августа Авито закрылся именно от этого"
echo "  адреса - но тогда ходили библиотекой, а не настоящим браузером."
echo

# xvfb-run здесь обязателен: он в CMD образа, а команда его подменяет.
# Без виртуального экрана браузер не запустится вовсе.
if docker compose run --rm avito \
       xvfb-run -a --server-args="-screen 0 1440x900x24" \
       python3 check_avito.py перфоратор; then
    echo
    echo "  Смотри строку «через разметку». Если карточек больше нуля - вышло."
else
    echo
    echo "  Проверка не прошла. Бота пока не запускаю - разбираемся."
    echo "  Покажи вывод выше целиком."
    exit 1
fi

# ---------- 5. Запуск ----------
say "5/5  Запускать бота?"
echo "  ВАЖНО: монитор должен работать в одном месте. Если он сейчас"
echo "  включён дома - выключи его там кнопкой «Выключить» и закрой окно."
echo "  Два разом - это вдвое больше обращений к Авито (дорога к капче)"
echo "  и два опроса Telegram, из-за чего сообщения теряются."
echo
read -r -p "  Запускаю? [y/N] " answer
case "$answer" in
    [yY]*)
        docker compose up -d
        echo
        echo "  Запущен. Что дальше:"
        echo "    docker compose logs --tail 30 | grep -v api.telegram.org"
        echo "    docker compose ps"
        echo
        echo "  В Telegram нажми «🟢 Включить» - и можно выключать компьютер."
        ;;
    *)
        echo "  Не запускаю. Когда решишь: docker compose up -d"
        ;;
esac
