FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot_5.py avito_bot.py avito_monitor.py test_avito_monitor.py ./

# База монитора Авито живёт здесь. Том обязателен: без него история цен
# стирается при каждом рестарте, а копится она неделями.
ENV AVITO_DB=/data/avito.db
VOLUME ["/data"]

# Образ один на оба бота, точка входа переопределяется в docker-compose.yml:
# bot_5.py - эксперт по слаботочке, avito_bot.py - перекуп.
CMD ["python3", "avito_bot.py"]
