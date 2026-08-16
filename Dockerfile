# Зеркало Яндекса вместо Docker Hub: сам Hub из России не открывается,
# образ тот же самый. Если зеркало недоступно - меняй эту строку на
# FROM python:3.11-slim или на mirror.gcr.io/library/python:3.11-slim
FROM cr.yandex/mirror/python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Браузер и виртуальный экран.
#
# Зачем экран на сервере, где смотреть некому. Авито пропускает только
# видимый браузер: скрытый режим распознаётся, проверено тремя способами
# (см. avito_browser.py). «Видимый» для браузера означает лишь одно - что
# ему есть куда рисовать. Xvfb даёт экран, которого нет физически: окно
# честно открывается, просто в память, а не на монитор. Для браузера это
# неотличимо от настоящего, и режим у него самый обычный, не скрытый.
#
# --with-deps подтягивает системные библиотеки, без которых Chromium не
# стартует. Их около сотни, руками перечислять - гиблое дело.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb \
    && python3 -m playwright install --with-deps chromium \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY avito_bot.py avito_monitor.py avito_browser.py resale_expert.py check_avito.py \
     env_file.py test_avito_monitor.py test_avito_bot.py test_resale_expert.py ./

# Читаем Авито браузером - так же, как дома.
ENV AVITO_FETCH=browser
# Режим самый обычный, не скрытый: скрытый Авито не пропускает.
ENV AVITO_BROWSER_HEADLESS=0
# Пустая строка - встроенный в Playwright Chromium. Дома вместо него берётся
# установленный Chrome, потому что встроенный на той машине не стартует
# (Windows жалуется на side-by-side). В Linux встроенный работает.
ENV AVITO_BROWSER_CHANNEL=""
# Профиль браузера - на том же томе, что и база. Иначе накопленные печенья
# Авито стирались бы при каждой пересборке, и каждый запуск начинался бы с
# капчи: чистый профиль защита не пускает.
ENV AVITO_BROWSER_PROFILE=/data/browser_profile

# База монитора живёт здесь. Том обязателен: без него история цен стирается
# при каждом рестарте, а копится она неделями.
ENV AVITO_DB=/data/avito.db
VOLUME ["/data"]

# xvfb-run поднимает виртуальный экран и запускает под ним бота.
# -a - сам подберёт свободный номер экрана, если первый занят.
CMD ["xvfb-run", "-a", "--server-args=-screen 0 1440x900x24", \
     "python3", "avito_bot.py"]
