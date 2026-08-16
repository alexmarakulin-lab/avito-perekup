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

# Токен спрашивать здесь нельзя: он длинный, а буфер VNC режет длинные
# строки - приедет обрубок, и разбираться потом будем полдня.
if ! grep -q "^AVITO_BOT_TOKEN=." .env; then
    echo "  не заполнен AVITO_BOT_TOKEN."
    echo "  Впиши его так (вместо ЗДЕСЬ подставь токен от @BotFather):"
    echo "     sed -i '/^AVITO_BOT_TOKEN=/d' .env; echo 'AVITO_BOT_TOKEN=ЗДЕСЬ' >> .env"
    exit 1
fi

# А номер короткий, его проще спросить, чем отправлять человека в nano:
# редактор в окне VNC - отдельное приключение со своими горячими клавишами.
if ! grep -q "^AVITO_OWNER_ID=." .env; then
    echo "  Не заполнен твой номер в Telegram - без него боту сможет писать кто угодно."
    echo "  Это короткое число из 9-10 цифр. Узнать: бот @userinfobot в Telegram."
    echo
    read -r -p "  Номер (Enter - пропустить и выйти): " owner
    if [ -z "${owner}" ] || ! echo "$owner" | grep -qE '^[0-9]+$'; then
        echo "  Нужны только цифры. Выхожу."
        exit 1
    fi
    # Сначала стираем старую строку, потом пишем новую: так неважно, была
    # она пустой или её не было вовсе.
    sed -i '/^AVITO_OWNER_ID=/d' .env
    echo "AVITO_OWNER_ID=${owner}" >> .env
    sed -i '/^AVITO_OWNER_CHAT=/d' .env
    echo "AVITO_OWNER_CHAT=${owner}" >> .env
    echo "  записал"
fi

# Куда слать находки. Обычно тот же номер, что и владелец, но строки может
# не быть вовсе - тогда монитор нашёл бы лот и не знал, кому о нём сказать.
if ! grep -q "^AVITO_OWNER_CHAT=." .env; then
    owner=$(grep '^AVITO_OWNER_ID=' .env | cut -d= -f2)
    sed -i '/^AVITO_OWNER_CHAT=/d' .env
    echo "AVITO_OWNER_CHAT=${owner}" >> .env
    echo "  проставил, куда слать находки: ${owner}"
fi

echo "  токен и номер владельца на месте"

# ---------- 3. Сборка ----------
say "3/5  Собираю образ. Это 5-15 минут, качается браузер"

# Связь с этого сервера рваная: и github.com, и pypi.org обрываются на
# середине сериями. С git это лечилось повтором - обычно проходило со
# второй попытки. Здесь то же самое, только автоматически.
#
# Третья попытка идёт через зеркало: если pypi.org закрыт не по случаю, а
# насовсем, повторять его бессмысленно, нужен другой адрес.
MIRROR_PIP="https://pypi.tuna.tsinghua.edu.cn/simple"
MIRROR_PW="https://npmmirror.com/mirrors/playwright/"

built=0
for attempt in 1 2 3; do
    if [ "$attempt" = 3 ]; then
        echo
        echo "  Попытка 3: беру библиотеки с зеркала, раз pypi.org не отвечает."
        args="--build-arg PIP_INDEX=${MIRROR_PIP} --build-arg PW_HOST=${MIRROR_PW}"
    else
        echo
        echo "  Попытка ${attempt} из 3..."
        args=""
    fi

    # shellcheck disable=SC2086
    if docker compose build $args; then
        built=1
        break
    fi
    [ "$attempt" -lt 3 ] && { echo "  Оборвалось. Жду 10 секунд и повторяю."; sleep 10; }
done

if [ "$built" != 1 ]; then
    echo
    echo "  Сборка не прошла три раза. Что смотреть в выводе выше:"
    echo "   - 'pypi.org ... Read timed out'  - не достучались до библиотек;"
    echo "     значит и зеркало закрыто, нужен прокси для сборки."
    echo "   - 'cr.yandex/mirror ...'         - недоступно зеркало образов;"
    echo "     тогда в Dockerfile первую строку замени на FROM python:3.11-slim"
    echo "   - 'No space left'                - кончилось место: docker system prune -a"
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
