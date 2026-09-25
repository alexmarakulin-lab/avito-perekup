# slabotochny-bot

Telegram-бот: слаботочные системы (СПС, СОУЭ, СКУД, CCTV, СКС), пивоварение и дистилляция, чтение PDF. Ответы генерирует Groq (Llama).

## Настройка

Ключи задаются переменными окружения, в коде их нет:

| Переменная | Обязательна | Где взять |
|---|---|---|
| `TELEGRAM_TOKEN` | да | @BotFather |
| `GROQ_API_KEY` | да | console.groq.com → API Keys |
| `SERPER_API_KEY` | нет, для кнопки «Новости» | serper.dev |
| `GROQ_MODELS` | нет | список моделей через запятую, первая - основная. По умолчанию `llama-3.3-70b-versatile,llama-3.1-8b-instant` |

## Запуск

Docker:

```bash
docker build -t slabotochny-bot .
docker run -d --restart unless-stopped \
  -e TELEGRAM_TOKEN=... \
  -e GROQ_API_KEY=... \
  slabotochny-bot
```

Без Docker:

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=... GROQ_API_KEY=...
python3 bot_5.py
```

На хостинге (Railway, Render, VPS-панель) те же переменные задаются в настройках сервиса (Variables / Environment).
