#!/usr/bin/env bash
# Настройка прокси для Telegram.
#
# Спрашивает данные прокси по частям, сам собирает строку подключения,
# перебирает способы подключения, прописывает рабочий в .env и перезапускает
# бота. Нужен, когда api.telegram.org с сервера не открывается напрямую.
#
# Запуск:  bash proxy.sh

set -u

GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; OFF=$'\033[0m'

say()  { echo "${GREEN}==>${OFF} $*"; }
warn() { echo "${YELLOW}!!${OFF}  $*"; }
die()  { echo "${RED}Ошибка:${OFF} $*" >&2; exit 1; }

read_line() {
    local reply=""
    if { exec 3</dev/tty; } 2>/dev/null; then
        read -r reply <&3 || reply=""
        exec 3<&-
    else
        read -r reply || reply=""
    fi
    printf '%s' "$reply"
}

ask() {  # ask "вопрос" -> печатает ответ без пробелов
    printf "${BOLD}%s${OFF} " "$1" >&2
    read_line | tr -d '[:space:]'
}

cd "$(dirname "$0")" || die "не могу перейти в папку проекта"
[ -f docker-compose.yml ] || die "запусти из папки проекта: cd ~/avito-perekup && bash proxy.sh"

echo
echo "${BOLD}Настройка прокси для Telegram${OFF}"
echo
echo "Данные возьми в кабинете продавца прокси. Нужны четыре значения."
echo "Если портов два - бери тот, что подписан SOCKS5."
echo

# ---------- 1. Инструменты ----------
missing=""
command -v curl >/dev/null 2>&1 || missing="$missing curl"
command -v nc   >/dev/null 2>&1 || missing="$missing netcat-openbsd"
if [ -n "$missing" ]; then
    say "Доставляю:$missing"
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $missing >/dev/null 2>&1 \
        || warn "не удалось поставить$missing, часть проверок пропущу"
fi

# ---------- 2. Данные ----------
HOST="$(ask 'Адрес прокси (например 185.10.20.30):')"
[ -n "$HOST" ] || die "адрес пустой"

PORT="$(ask 'Порт SOCKS5 (например 1080):')"
case "$PORT" in
    ''|*[!0-9]*) die "порт должен быть числом, получено: $PORT" ;;
esac

USER_NAME="$(ask 'Логин:')"
PASS="$(ask 'Пароль:')"

echo
say "Собрал: ${HOST}:${PORT}, логин ${USER_NAME:-<пусто>}"

# ---------- 3. Достучаться до прокси ----------
echo
say "Проверяю, отвечает ли прокси на этом порту..."
if command -v nc >/dev/null 2>&1; then
    if nc -z -w 10 "$HOST" "$PORT" 2>/dev/null; then
        say "Порт открыт, прокси отвечает"
    else
        warn "Порт ${PORT} на ${HOST} не отвечает"
        echo
        echo "Возможные причины:"
        echo "  - взят порт для HTTP вместо SOCKS5 (посмотри в кабинете оба)"
        echo "  - у прокси включена авторизация по IP, а адрес сервера не разрешён"
        echo "    (добавь в кабинете продавца адрес этого сервера)"
        echo "  - этот порт режет фильтрация провайдера"
        echo
        printf "${BOLD}Всё равно попробовать подключиться? [y/N]:${OFF} "
        [ "$(read_line)" = "y" ] || exit 1
    fi
fi

# ---------- 4. Перебор способов ----------
if [ -n "$USER_NAME" ]; then
    CRED="${USER_NAME}:${PASS}@"
else
    CRED=""
fi

WORKING=""
for scheme in socks5h socks5 http; do
    URL="${scheme}://${CRED}${HOST}:${PORT}"
    printf "    пробую %-8s ... " "$scheme"
    OUT="$(curl -sS --max-time 25 --proxy "$URL" https://api.telegram.org/ 2>&1)"
    case "$OUT" in
        *'"ok"'*)
            echo "${GREEN}работает${OFF}"
            WORKING="$URL"
            break
            ;;
        *)
            # Причину печатаем сразу: без неё непонятно, отказал прокси
            # в доступе или соединение вообще не дошло.
            echo "${RED}нет${OFF} — $(printf '%s' "$OUT" | head -1 | cut -c1-70)"
            ;;
    esac
done

if [ -z "$WORKING" ]; then
    echo
    warn "Ни один способ не сработал."
    echo
    echo "Как читать причины выше:"
    echo "  'Proxy CONNECT aborted'   - прокси ответил, но не пустил:"
    echo "                              неверные логин с паролем либо адрес"
    echo "                              этого сервера не разрешён в кабинете"
    echo "  'Connection timed out'    - прокси молчит: скорее всего взят порт"
    echo "                              не того протокола"
    echo "  'Could not resolve proxy' - опечатка в адресе прокси"
    echo
    echo "Адрес этого сервера, который нужно разрешить в кабинете продавца:"
    ip -4 addr show scope global 2>/dev/null \
        | awk '/inet /{print "    " $2}' | cut -d/ -f1 || true
    exit 1
fi

echo
say "${BOLD}Прокси работает.${OFF} Способ: ${WORKING%%:*}"

# ---------- 5. Записать в .env ----------
[ -f .env ] || die ".env не найден, сначала пройди установку через setup.sh"

cp .env .env.backup
if grep -q '^TELEGRAM_PROXY=' .env; then
    grep -v '^TELEGRAM_PROXY=' .env > .env.tmp
    mv .env.tmp .env
fi
echo "TELEGRAM_PROXY=${WORKING}" >> .env
chmod 600 .env
say "Прописал в .env (старая копия - .env.backup)"

# ---------- 6. Перезапуск ----------
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
else
    COMPOSE="docker-compose"
fi

echo
say "Пересобираю и перезапускаю бота..."
$COMPOSE up -d --build || die "не удалось перезапустить, смотри вывод выше"

sleep 8

echo
if $COMPOSE logs --tail 40 2>/dev/null | grep -q "работаю через прокси"; then
    say "${BOLD}Готово. Бот запущен и работает через прокси.${OFF}"
else
    warn "Бот перезапущен, но строки про прокси в логе пока нет."
    echo "    Посмотри сам: ${COMPOSE} logs --tail 20"
fi

cat << 'FINAL'

Что дальше:

  1. Открой бота в Telegram и отправь /start
     Должно прийти приветствие с кнопками.

  2. Отправь /myid, получишь число.

  3. Впиши его: nano .env -> строка AVITO_OWNER_ID=
     (сохранить: Ctrl+O, Enter, потом Ctrl+X)

  4. Примени: docker compose up -d

  5. Нажми кнопку «Проверить Авито» и покажи ответ.

FINAL
