# slabotochny-bot
## Поиск участков на Avito (`avito_land.py`)

Парсер земельных участков вокруг Краснодара под постройку дома.

```bash
pip install -r requirements-avito.txt
playwright install chromium
python3 avito_land.py --izhs-only --max-price 600000 --max-minutes 60 --pages 15
```

Результат — CSV (`uchastki.csv`) и топ-30 в консоли, отсортированные по цене за
сотку. Время в пути считается по словарю `DRIVE_MINUTES` в начале файла —
дополняйте его своими населёнными пунктами.

Если Avito показывает капчу, перезапустите с `--headful` и пройдите её вручную:
контекст браузера остаётся живым и парсинг продолжится.
