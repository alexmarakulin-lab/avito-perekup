# Зеркало Яндекса вместо Docker Hub: сам Hub из России не открывается,
# образ тот же самый. Если зеркало недоступно - меняй эту строку на
# FROM python:3.11-slim или на mirror.gcr.io/library/python:3.11-slim
FROM cr.yandex/mirror/python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY avito_bot.py avito_monitor.py resale_expert.py \
     test_avito_monitor.py test_avito_bot.py test_resale_expert.py ./

# База монитора живёт здесь. Том обязателен: без него история цен стирается
# при каждом рестарте, а копится она неделями.
ENV AVITO_DB=/data/avito.db
VOLUME ["/data"]

CMD ["python3", "avito_bot.py"]
