"""Цены конкурентов и похожие закупки из открытых данных ЕИС (zakupki.gov.ru).

Что делаем по каждой крупной позиции тендера:
  1. ищем в реестре контрактов 44-ФЗ контракты с похожим предметом;
  2. открываем до N карточек: цена за единицу по похожей позиции, регион заказчика, поставщик;
  3. считаем медиану, минимум и максимум отдельно по региону тендера и по всей России.
Плюс история закупок заказчика (по ИНН) для краткой аналитики.

Всё из публичных страниц ЕИС, без входа. Разбор устойчив к мелким изменениям вёрстки:
опирается на подписи полей и ссылки, а не на точную структуру HTML.
"""

import re
import statistics
import time
import urllib.parse

import tparse as P

EIS = "https://zakupki.gov.ru"
CONTRACT_SEARCH = EIS + "/epz/contract/search/results.html"
NOTICE_SEARCH = EIS + "/epz/order/extendedsearch/results.html"


def contract_search_url(query):
    return CONTRACT_SEARCH + "?" + urllib.parse.urlencode({
        "searchString": query, "morphology": "on", "fz44": "on", "pageNumber": "1",
        "sortDirection": "false", "recordsPerPage": "_20", "sortBy": "UPDATE_DATE",
    })


def notice_search_url(query):
    return NOTICE_SEARCH + "?" + urllib.parse.urlencode({
        "searchString": query, "morphology": "on", "fz44": "on", "fz223": "on", "pageNumber": "1",
        "sortDirection": "false", "recordsPerPage": "_20", "sortBy": "UPDATE_DATE",
    })


def contract_items_url(card_url):
    return re.sub(r"/(common-info|[a-z-]+)\.html", "/payment-info-and-target-of-order.html", card_url, count=1)


# ---------------------------------------------------------------- разбор страниц ЕИС

def parse_contract_results(html, base=EIS):
    """Номера и ссылки контрактов из выдачи поиска (без дублей, по порядку)."""
    page = P.parse_html(html)
    out, seen = [], set()
    for l in page.links:
        href = l.get("href") or ""
        m = re.search(r"reestrNumber=(\d{10,25})", href)
        if not m or "contract" not in href.lower():
            continue
        num = m.group(1)
        if num in seen:
            continue
        seen.add(num)
        url = urllib.parse.urljoin(base, href)
        if "contractCard" not in url:
            url = EIS + "/epz/contract/contractCard/common-info.html?reestrNumber=" + num
        out.append({"number": num, "url": url})
    return out


def parse_notice_results(html, base=EIS):
    """Закупки из выдачи поиска извещений: номер, ссылка, предмет, статус, НМЦК."""
    page = P.parse_html(html)
    lines = page.lines
    out, seen = [], set()
    for l in page.links:
        href = l.get("href") or ""
        m = re.search(r"regNumber=(\d{8,25})", href)
        if not m or "/notice/" not in href:
            continue
        num = m.group(1)
        if num in seen:
            continue
        seen.add(num)
        out.append({"number": num, "url": urllib.parse.urljoin(base, href)})
    # Поля каждой карточки идут в тексте после строки с её номером.
    text_idx = []
    for i, line in enumerate(lines):
        for r in out:
            if r["number"] in line and "i" not in r:
                r["i"] = i
                text_idx.append(i)
    text_idx.sort()
    for r in out:
        i = r.pop("i", None)
        if i is None:
            continue
        nxt = min([j for j in text_idx if j > i] or [len(lines)])
        block = lines[i:nxt]
        r["status"] = ""
        for s in block[:4]:
            if re.search(r"(подача заявок|работа комиссии|завершен|отменен|приостановлен|определени\w+ поставщика)", s, re.I):
                r["status"] = s[:80]
                break
        r["subject"] = P.norm(P.find_value(block, [r"объект закупки", r"предмет"], P.IS_TEXT) or "")[:200]
        r["nmck"] = P.money_value(P.find_value(block, [r"начальная цена", r"начальная \(максимальная\) цена"], P.HAS_NUM))
        r["date"] = P.norm(P.find_value(block, [r"размещено"], P.HAS_DATE) or "")[:20]
    return out


def parse_contract_card(common_html, items_html):
    """Цена контракта, дата, заказчик и его регион, поставщик, позиции с ценой за единицу."""
    c = P.parse_html(common_html)
    lines = c.lines
    price = P.money_value(P.find_value(lines, [r"цена контракта", r"сумма контракта"], P.HAS_NUM))
    date = P.norm(P.find_value(lines, [r"дата заключения контракта", r"заключ[её]н", r"дата заключения"], P.HAS_DATE) or "")
    customer = P.norm(P.find_value(lines, [r"полное наименование заказчика", r"^заказчик\w*\s*$", r"наименование заказчика"], P.IS_TEXT) or "")
    address = P.norm(P.find_value(lines, [r"место нахождения", r"адрес места нахождения", r"почтовый адрес", r"регион"], P.IS_TEXT) or "")
    supplier = ""
    for rows in c.tables:
        if not rows:
            continue
        header = " ".join(rows[0]).lower()
        if "поставщик" in header or "наименование юридического лица" in header or "организации" in header:
            for r in rows[1:]:
                cell = next((x for x in r if re.search(r"(ООО|АО|ПАО|ЗАО|ИП|общество|предприниматель)", x, re.I)), "")
                if cell:
                    supplier = P.norm(cell)[:160]
                    break
        if supplier:
            break
    if not supplier:
        supplier = P.norm(P.find_value(lines, [r"информация о поставщик\w*", r"^поставщик\w*\s*$"], P.IS_TEXT) or "")[:160]
    items = P.items_from_tables(P.parse_html(items_html).tables) if items_html else []
    region_key, region_name = P.region_of(address, customer)
    return {"price": price, "date": date[:20], "customer": customer[:200], "region": region_name,
            "regionKey": region_key, "supplier": supplier, "items": items}


# ---------------------------------------------------------------- статистика

def stats(values):
    v = sorted(x for x in values if x and x > 0)
    if not v:
        return None
    return {"n": len(v), "median": round(statistics.median(v), 2), "min": round(v[0], 2), "max": round(v[-1], 2)}


def matches_for(item, contracts, min_sim=0.5):
    """Похожие позиции из карточек контрактов с ценой за единицу в той же единице измерения."""
    unit = P.unit_key(item.get("unit") or "")
    out = []
    for c in contracts:
        best = None
        for row in c.get("items") or []:
            if not row.get("nmckUnit"):
                continue
            if unit and P.unit_key(row.get("unit") or "") not in (unit, ""):
                continue
            sim = P.similarity(item["name"], row["name"])
            if sim >= min_sim and (best is None or sim > best[0]):
                best = (sim, row)
        if best:
            out.append({"price": best[1]["nmckUnit"], "name": best[1]["name"][:160], "unit": best[1].get("unit", ""),
                        "contract": c["number"], "url": c["url"], "date": c.get("date", ""), "region": c.get("region", ""),
                        "regionKey": c.get("regionKey", ""), "supplier": c.get("supplier", ""), "customer": c.get("customer", "")})
    return out


def summarize(item, matches, region_key):
    reg = [m["price"] for m in matches if region_key and m["regionKey"] == region_key]
    return {"region": stats(reg), "rf": stats([m["price"] for m in matches])}


# ---------------------------------------------------------------- запуск

def analyze(tender, fetch, decode, progress=lambda *a: None, max_items=5, max_cards=8, pause=0.7):
    """tender: {"items":[{"name","unit","qty","nmckUnit"}], "place", "customer", "inn"}.
    fetch(url) -> (final_url, headers, bytes); decode(headers, bytes) -> str."""
    region_key, region_name = P.region_of(tender.get("place", ""), tender.get("customer", ""))
    items = [i for i in tender.get("items") or [] if (i.get("name") or "").strip() and (i.get("qty") or 0) > 0]
    weight = lambda i: (i.get("qty") or 0) * (i.get("nmckUnit") or i.get("cost") or 1)
    items = sorted(items, key=weight, reverse=True)[:max_items]
    result = {"region": region_name, "regionKey": region_key, "items": [], "customer": None, "errors": [], "at": int(time.time())}
    cache = {}

    def get(url):
        if url not in cache:
            time.sleep(pause)
            _, h, b = fetch(url)
            cache[url] = decode(h, b)
        return cache[url]

    total = len(items) + (1 if tender.get("inn") else 0)
    done = 0
    for item in items:
        query = P.search_query(item["name"])
        entry = {"id": item.get("id"), "name": item["name"], "unit": item.get("unit", ""), "query": query,
                 "searchUrl": contract_search_url(query), "matches": [], "region": None, "rf": None}
        try:
            progress(done, total, f"Ищу контракты: {query}")
            found = parse_contract_results(get(contract_search_url(query)))[:max_cards]
            cards = []
            for c in found:
                try:
                    common = get(c["url"])
                    items_html = get(contract_items_url(c["url"]))
                    card = parse_contract_card(common, items_html)
                    card.update(c)
                    cards.append(card)
                except Exception as e:  # одна карточка не должна ломать весь поиск
                    result["errors"].append(f"{c['number']}: {e}")
            entry["matches"] = matches_for(item, cards)
            entry.update(summarize(item, entry["matches"], region_key))
            entry["contracts"] = len(cards)
        except Exception as e:
            entry["error"] = str(e)
        result["items"].append(entry)
        done += 1

    inn = tender.get("inn")
    if inn:
        try:
            progress(done, total, "История закупок заказчика")
            rows = parse_notice_results(get(notice_search_url(inn)))[:20]
            canceled = sum(1 for r in rows if re.search(r"отмен", r.get("status", ""), re.I))
            result["customer"] = {"inn": inn, "url": notice_search_url(inn), "purchases": rows, "count": len(rows), "canceled": canceled}
        except Exception as e:
            result["errors"].append(f"заказчик: {e}")
    progress(total, total, "Готово")
    return result
