# Зеркало Яндекса вместо Docker Hub: сам Hub из России не открывается,
# образ тот же самый. Если зеркало недоступно - меняй эту строку на
# FROM python:3.11-slim или на mirror.gcr.io/library/python:3.11-slim
FROM cr.yandex/mirror/python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY avito_bot.py avito_monitor.py avito_browser.py resale_expert.py check_avito.py \
     env_file.py test_avito_monitor.py test_avito_bot.py test_resale_expert.py ./

# Внимание: в контейнере Авито не читается. Проходит только настоящий
# видимый браузер (разбор в avito_browser.py), а открыть окно на сервере
# негде. Здесь работает всё остальное - команды, консультант, база.
# Монитор находок живёт на домашнем компьютере.
ENV AVITO_FETCH=http

# База монитора живёт здесь. Том обязателен: без него история цен стирается
# при каждом рестарте, а копится она неделями.
ENV AVITO_DB=/data/avito.db
VOLUME ["/data"]

CMD ["python3", "avito_bot.py"]
