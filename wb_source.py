# -*- coding: utf-8 -*-
"""Wildberries как вторая площадка.

Что проверено 13.08.2026, прежде чем писать этот файл.

Обычным запросом ни одна площадка не читается: Wildberries отвечает 429,
Ozon - 403, Яндекс Маркет - 403 со страницей «Похоже, вы используете VPN».
Настоящим браузером - тем самым, что открывает Авито, - **проходит только
Wildberries**: 40 карточек, всё нужное на месте. Ozon отдаёт «Похоже, нет
соединения», Маркет упирается в свою проверку. Поэтому здесь один
Wildberries, а не три площадки: обещать остальные значило бы звать на
четвёртый круг того же, на чём мы уже потеряли неделю с Авито.

Отдельно про «скидки». На карточке iPhone 15 стоит 41 468 ₽ «вместо
106 058 ₽», то есть −60%. Новый iPhone 15 столько никогда не стоил:
**старая цена на Wildberries нарисованная**. Считать выгоду по их
собственной скидке - врать себе, поэтому old_price мы забираем только для
показа, а судим по накопленной истории, как и на Авито.

Имена классов сняты с живой страницы. У Wildberries они с хвостами вида
brandContainer--b1Fxa: хвост меняется при каждом их обновлении, поэтому
ищем по вхождению, а не по точному совпадению.
"""
import logging
import re
from urllib.parse import quote

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

logger = logging.getLogger(__name__)

# Товары дорисовываются javascript'ом; ждём появления первой карточки.
CARDS_SELECTOR = "article"

ITEM_ID_RE = re.compile(r"/catalog/(\d+)/")


def build_search_url(query: str) -> str:
    """Адрес поиска. sort=newly - сначала новинки, как s=104 у Авито."""
    return ("https://www.wildberries.ru/catalog/0/search.aspx"
            f"?search={quote(query)}&sort=newly")


def _price(text: str) -> int:
    """Из «41 468 ₽» делает 41468. Ноль - цену вытащить не смогли."""
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def item_url(href: str) -> str:
    if not href:
        return ""
    return href if href.startswith("http") else f"https://www.wildberries.ru{href}"


def parse(html: str) -> list:
    """Разбирает выдачу Wildberries в тот же вид, что и выдачу Авито.

    Ключи словаря намеренно те же самые: так вся дальнейшая машинерия -
    отбор, медианы, база, уведомления - работает без единой правки.
    """
    if not BS4_AVAILABLE:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.select("article"):
        link = card.select_one('a[href*="/catalog/"]')
        if not link:
            continue
        href = link.get("href", "")
        found = ITEM_ID_RE.search(href)
        if not found:
            continue

        # Цена и зачёркнутая цена лежат рядом: ins - нынешняя, del - «старая».
        price_box = card.select_one('[class*="product-card__price"]')
        current = price_box.select_one("ins") if price_box else None
        old = price_box.select_one("del") if price_box else None

        # Марка и название - соседи внутри общей обёртки, между ними «/».
        brand_el = card.select_one('[class*="brandContainer"]')
        brand, name = "", ""
        if brand_el is not None:
            parts = [t.strip() for t in brand_el.parent.stripped_strings
                     if t.strip() and t.strip() != "/"]
            if parts:
                brand, name = parts[0], " ".join(parts[1:])

        title = " ".join(x for x in (brand, name) if x).strip()
        if not title:
            continue

        rating_el = card.select_one('[class*="ratingContent"]')

        items.append({
            "item_id": f"wb{found.group(1)}",   # чтобы не столкнуться с номерами Авито
            "title": title,
            "price": _price(current.get_text() if current else ""),
            "url": item_url(href.split("?")[0]),
            "address": "",                      # у маркетплейса адреса нет
            "age_min": None,                    # и времени публикации тоже
            "seller_score": rating_el.get_text(" ", strip=True) if rating_el else "",
            "seller_reviews": "",
            # Только для показа. Судить по ней нельзя - см. заголовок файла.
            "old_price": _price(old.get_text() if old else ""),
        })

    if items:
        logger.debug(f"Wildberries: разобрано {len(items)} карточек")
    return items


def is_blocked(html: str) -> bool:
    """Похоже ли на заслон, а не на выдачу."""
    low = html.lower()
    return ("<article" not in low
            and ("вы не робот" in low or "captcha" in low
                 or "доступ ограничен" in low or "нет соединения" in low))
