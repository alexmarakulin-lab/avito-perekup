# slabotochny-bot

Telegram-бот: эксперт по слаботочным системам, пивоварению и дистилляции
плюс монитор Авито для перекупа по Краснодару.

- `bot_5.py` — сам бот (ответы через Groq, разбор PDF, поиск новостей)
- `avito_monitor.py` — монитор Авито: следит за новыми лотами, копит историю
  цен, шлёт находки дешевле рынка. Описание и развёртывание — [AVITO.md](AVITO.md)
- `test_avito_monitor.py` — оффлайн-проверка логики монитора, сеть не нужна

Запуск: `docker build -t slabotochny-bot . && docker run -d --restart unless-stopped -v /opt/bot-data:/data -e TELEGRAM_TOKEN=... -e GROQ_API_KEY=... slabotochny-bot`
