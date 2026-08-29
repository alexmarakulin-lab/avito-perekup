# -*- coding: utf-8 -*-
"""
Поиск земельных участков на Avito вокруг Краснодара (до ~1 часа езды),
пригодных под постройку дома.

Зачем отдельный скрипт: Avito отдаёт выдачу только браузеру с JS и живыми
куками, обычный requests/httpx получает заглушку антибота. Поэтому здесь
Playwright с настоящим Chromium.

Запуск:
    pip install -r requirements-avito.txt
    playwright install chromium
    python3 avito_land.py --max-price 600000 --pages 10 --out uchastki.csv

Полезные флаги:
    --headful           показать окно браузера (помогает, если Avito просит капчу)
    --max-minutes 60    порог времени в пути от Краснодара
    --izhs-only         только ИЖС/ЛПХ, без СНТ и сельхозки
    --min-sotok 4       отсечь совсем мелкие нарезки
"""

import argparse
import csv
import random
import re
import sys
import time

BASE_URL = "https://www.avito.ru/krasnodarskiy_kray/zemelnye_uchastki"

# Ориентировочное время в пути на машине от центра Краснодара, минуты.
# Пополняйте под себя: фильтр работает по вхождению ключа в адрес объявления.
DRIVE_MINUTES = {
    # город и пригороды
    "краснодар": 20, "пашковский": 25, "калинино": 25, "яблоновский": 25,
    "новая адыгея": 25, "старобжегокай": 30, "энем": 30, "афипсип": 35,
    "индустриальный": 25, "лорис": 25, "знаменский": 30, "березовый": 25,
    "елизаветинская": 35, "старокорсунская": 35, "васюринская": 45,
    "белозерный": 30, "плодородный": 25, "дружелюбный": 30, "копанской": 40,
    # Динской район
    "динская": 35, "новотитаровская": 30, "пластуновская": 45,
    "васильевское": 40, "старомышастовская": 45, "нововеличковская": 45,
    "агроном": 40, "южный": 35,
    # Северский район
    "северская": 45, "афипский": 30, "смоленская": 45, "ильский": 50,
    "азовская": 50, "новодмитриевская": 40, "григорьевская": 55,
    "черноморский": 60, "львовское": 40,
    # Красноармейский / Калининский
    "марьянская": 45, "полтавская": 60, "старонижестеблиевская": 55,
    "ивановская": 55, "калининская": 70,
    # Усть-Лабинский / Кореновский / Тимашевский
    "усть-лабинск": 60, "воронежская": 50, "ладожская": 70,
    "кореновск": 60, "платнировская": 50, "дядьковская": 50,
    "тимашевск": 60, "медведовская": 50, "днепровская": 55, "роговская": 70,
    # Абинский / Крымский / Горячий Ключ / Белореченский
    "абинск": 65, "холмская": 55, "ахтырский": 65,
    "крымск": 75, "варениковская": 90,
    "горячий ключ": 55, "саратовская": 45, "бакинская": 65,
    "белореченск": 75, "рязанская": 55,
    # Апшеронский / Выселковский — обычно уже за часом, оставлены для справки
    "выселки": 75, "березанская": 70, "апшеронск": 100,
}

# Категории по назначению: что можно строить.
CATEGORY_PATTERNS = [
    ("ИЖС", r"\bижс\b"),
    ("ЛПХ", r"\bлпх\b|личное подсоб"),
    ("СНТ/ДНП", r"\bснт\b|\bднп\b|\bдachn|дачн|садовод"),
    ("Промназначения", r"пром"),
    ("Сельхоз", r"сельхоз|\bсх\b|фермер"),
]
BUILDABLE = {"ИЖС", "ЛПХ"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def classify(title: str) -> str:
    low = title.lower()
    for name, pattern in CATEGORY_PATTERNS:
        if re.search(pattern, low):
            return name
    return "не указано"


def parse_sotki(title: str):
    """Площадь в сотках из заголовка вида 'Участок 6,5 сот. (ИЖС)'."""
    m = re.search(r"([\d]+(?:[.,]\d+)?)\s*сот", title.lower())
    if m:
        return float(m.group(1).replace(",", "."))
    m = re.search(r"([\d]+(?:[.,]\d+)?)\s*га", title.lower())
    if m:
        return float(m.group(1).replace(",", ".")) * 100
    return None


def parse_price(text: str):
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def drive_time(address: str):
    """Минуты в пути от Краснодара по названию населённого пункта в адресе."""
    low = (address or "").lower()
    best = None
    for name, minutes in DRIVE_MINUTES.items():
        if name in low:
            if best is None or minutes < best:
                best = minutes
    return best


def collect_page(page):
    """Вытащить карточки объявлений с текущей страницы выдачи."""
    js = """
    () => Array.from(document.querySelectorAll('[data-marker="item"]')).map(el => {
        const q = (sel, attr) => {
            const n = el.querySelector(sel);
            if (!n) return '';
            return attr ? (n.getAttribute(attr) || '') : (n.textContent || '').trim();
        };
        return {
            id: el.getAttribute('data-item-id') || '',
            title: q('[itemprop="name"]') || q('[data-marker="item-title"]'),
            href: q('[data-marker="item-title"]', 'href') || q('a[itemprop="url"]', 'href'),
            price: q('meta[itemprop="price"]', 'content') || q('[data-marker="item-price"]'),
            address: q('[data-marker="item-address"]') || q('[class*="geo-address"]'),
            geo: q('[data-marker="item-address"]') + ' ' + q('[class*="geo-georeferences"]'),
            seller: q('[data-marker="seller-info/name"]'),
            date: q('[data-marker="item-date"]'),
        };
    })
    """
    return page.evaluate(js)


def scrape(args):
    from playwright.sync_api import sync_playwright

    rows, seen = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        ctx = browser.new_context(
            user_agent=UA,
            locale="ru-RU",
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.new_page()

        for page_no in range(1, args.pages + 1):
            url = (f"{BASE_URL}?pmax={args.max_price}&s=104&p={page_no}")
            print(f"[{page_no}/{args.pages}] {url}", file=sys.stderr)
            try:
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_selector('[data-marker="item"]', timeout=30000)
            except Exception as e:
                print(f"  страница не открылась: {e}", file=sys.stderr)
                if "captcha" in page.content().lower():
                    print("  Avito показал капчу. Перезапустите с --headful "
                          "и пройдите её руками.", file=sys.stderr)
                break

            # подгрузить ленивые карточки
            page.mouse.wheel(0, 4000)
            time.sleep(1.0)

            items = collect_page(page)
            if not items:
                print("  пусто — вероятно, выдача закончилась", file=sys.stderr)
                break

            for it in items:
                if it["id"] in seen:
                    continue
                seen.add(it["id"])
                rows.append(it)

            time.sleep(random.uniform(*args.delay_range))

        browser.close()
    return rows


def enrich_and_filter(rows, args):
    out = []
    for it in rows:
        title = it["title"]
        price = parse_price(it["price"])
        if price is None or price > args.max_price:
            continue
        if price < args.min_price:
            continue

        address = it["address"] or it["geo"]
        minutes = drive_time(address)
        if minutes is None:
            if not args.keep_unknown:
                continue
        elif minutes > args.max_minutes:
            continue

        category = classify(title)
        if args.izhs_only and category not in BUILDABLE:
            continue

        sotki = parse_sotki(title)
        if sotki is not None and sotki < args.min_sotok:
            continue

        href = it["href"] or ""
        if href.startswith("/"):
            href = "https://www.avito.ru" + href

        out.append({
            "цена": price,
            "соток": sotki or "",
            "цена_за_сотку": round(price / sotki) if sotki else "",
            "минут_от_краснодара": minutes if minutes is not None else "",
            "назначение": category,
            "заголовок": title,
            "адрес": address,
            "продавец": it["seller"],
            "дата": it["date"],
            "ссылка": href,
        })

    key = {"price": lambda r: r["цена"],
           "per_sotka": lambda r: r["цена_за_сотку"] or 10**9,
           "time": lambda r: r["минут_от_краснодара"] or 999}[args.sort]
    out.sort(key=key)
    return out


def main():
    ap = argparse.ArgumentParser(description="Участки под дом вокруг Краснодара с Avito")
    ap.add_argument("--max-price", type=int, default=600_000)
    ap.add_argument("--min-price", type=int, default=50_000,
                    help="отсечь неадекватно дешёвые (часто это доли или ошибки)")
    ap.add_argument("--max-minutes", type=int, default=60,
                    help="максимум времени в пути от Краснодара")
    ap.add_argument("--min-sotok", type=float, default=3.0)
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--izhs-only", action="store_true",
                    help="только ИЖС и ЛПХ — где законно строить жилой дом")
    ap.add_argument("--keep-unknown", action="store_true",
                    help="оставлять объявления с неопознанным населённым пунктом")
    ap.add_argument("--sort", choices=["price", "per_sotka", "time"], default="per_sotka")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--delay-range", type=float, nargs=2, default=(2.0, 5.0),
                    metavar=("MIN", "MAX"), help="пауза между страницами, сек")
    ap.add_argument("--out", default="uchastki.csv")
    args = ap.parse_args()

    raw = scrape(args)
    print(f"\nСобрано карточек: {len(raw)}", file=sys.stderr)
    rows = enrich_and_filter(raw, args)
    print(f"После фильтров: {len(rows)}\n", file=sys.stderr)

    if not rows:
        print("Ничего не подошло. Попробуйте --keep-unknown, больше --pages "
              "или поднять --max-price.")
        return

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    for r in rows[:30]:
        sot = f"{r['соток']} сот." if r["соток"] else "площадь н/д"
        per = f"{r['цена_за_сотку']} ₽/сот." if r["цена_за_сотку"] else ""
        mins = f"{r['минут_от_краснодара']} мин" if r["минут_от_краснодара"] else "время н/д"
        print(f"{r['цена']:>9,} ₽  {sot:>12}  {per:>14}  {mins:>8}  "
              f"{r['назначение']:<12} {r['адрес']}\n           {r['ссылка']}"
              .replace(",", " "))

    print(f"\nВсё выгружено в {args.out}")


if __name__ == "__main__":
    main()
