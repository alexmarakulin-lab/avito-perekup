FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot_5.py avito_monitor.py test_avito_monitor.py ./

# База монитора Авито живёт здесь. Том обязателен: без него история цен
# стирается при каждом рестарте, а копится она неделями.
ENV AVITO_DB=/data/avito.db
VOLUME ["/data"]

CMD ["python3", "bot_5.py"]
