#!/usr/bin/env bash
# Установка бота-перекупа на чистый сервер.
# Ставит docker, спрашивает токен, создаёт .env и запускает бота.
# Запускать из папки с проектом:  bash setup.sh

set -u

GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; OFF=$'\033[0m'

say()  { echo "${GREEN}==>${OFF} $*"; }
warn() { echo "${YELLOW}!!${OFF}  $*"; }
die()  { echo "${RED}Ошибка:${OFF} $*" >&2; exit 1; }

# Читает строку с клавиатуры. Через терминал, если он есть, иначе с обычного
# ввода - чтобы скрипт можно было прогонять автоматом.
read_line() {
    local reply=""
    # Проверяем терминал именно попыткой открыть: файл /dev/tty может
    # существовать, но не открываться - тогда читаем с обычного ввода.
    if { exec 3</dev/tty; } 2>/dev/null; then
        read -r reply <&3 || reply=""
        exec 3<&-
    else
        read -r reply || reply=""
    fi
    printf '%s' "$reply"
}

cd "$(dirname "$0")" || die "не могу перейти в папку проекта"

[ -f docker-compose.yml ] || die "запусти скрипт из папки проекта (там, где лежит docker-compose.yml)"

echo
echo "${BOLD}Установка бота-перекупа${OFF}"
echo

# ---------- 1. Docker ----------
if command -v docker >/dev/null 2>&1; then
    say "Docker уже стоит"
else
    say "Ставлю Docker, это займёт пару минут..."
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq || die "apt-get update не прошёл"
        apt-get install -y -qq docker.io docker-compose-plugin >/dev/null \
            || die "не удалось поставить Docker"
    else
        die "не Ubuntu/Debian — поставь Docker вручную и запусти скрипт снова"
    fi
    say "Docker поставлен"
fi

# Проверяем, что docker compose (новый, через пробел) доступен.
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    die "docker compose не найден. Поставь: apt-get install -y docker-compose-plugin"
fi

# ---------- 2. Токен ----------
if [ -f .env ]; then
    warn ".env уже есть."
    printf "Перезаписать его заново? Старые настройки потеряются [y/N]: "
    overwrite="$(read_line)"
    if [ "${overwrite}" != "y" ] && [ "${overwrite}" != "Y" ]; then
        say "Оставляю .env как есть"
        SKIP_ENV=1
    fi
fi

if [ "${SKIP_ENV:-0}" != "1" ]; then
    echo
    echo "Токен бота — та длинная строка от @BotFather вида 8649312428:AAEzz..."
    printf "${BOLD}Вставь токен:${OFF} "
    token="$(read_line | tr -d '[:space:]')"

    [ -n "$token" ] || die "токен пустой"
    case "$token" in
        *:*) : ;;
        *) die "это не похоже на токен — в нём должно быть двоеточие" ;;
    esac

    echo
    echo "Ключ Groq включает консультанта по сделкам (бесплатно на console.groq.com)."
    echo "Можно пропустить — просто нажми Enter, монитор будет работать и без него."
    printf "${BOLD}Ключ Groq:${OFF} "
    groq="$(read_line | tr -d '[:space:]')"

    cat > .env << ENVFILE
# Создан скриптом setup.sh. Правится обычным nano .env
AVITO_BOT_TOKEN=${token}
GROQ_API_KEY=${groq}

# Твой Telegram ID. Заполним после первого запуска: напиши боту /myid
AVITO_OWNER_ID=

AVITO_MAX_PRICE=5000
AVITO_REGION=krasnodar
AVITO_PROXY=
ENVFILE
    chmod 600 .env
    say "Файл .env создан, токен внутри"
fi

# ---------- 3. Запуск ----------
say "Собираю образ и запускаю бота..."
$COMPOSE up -d --build || die "не удалось запустить. Смотри вывод выше"

sleep 5
echo
if [ -n "$($COMPOSE ps -q avito 2>/dev/null)" ] && \
   [ "$(docker inspect -f '{{.State.Running}}' "$($COMPOSE ps -q avito)" 2>/dev/null)" = "true" ]; then
    say "${BOLD}Бот запущен.${OFF}"
else
    warn "Контейнер не поднялся. Логи:"
    $COMPOSE logs --tail 30
    exit 1
fi

cat << 'FINAL'

Что дальше:

  1. Открой своего бота в Telegram и нажми Start
  2. Отправь /myid — придёт число
  3. Впиши его: nano .env  ->  строка AVITO_OWNER_ID=
     (сохранить: Ctrl+O, Enter, потом Ctrl+X)
  4. Примени: docker compose up -d
  5. Нажми кнопку «Проверить Авито» — покажет, читается ли выдача
  6. Нажми «Включить»

Полезное:
  docker compose logs -f     смотреть логи (выход Ctrl+C)
  docker compose restart     перезапустить
  docker compose down        остановить

FINAL
