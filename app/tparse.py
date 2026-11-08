"""Разбор страниц закупок: ЕИС (zakupki.gov.ru), Росэлторг, B2B-Center и любые похожие.

Только стандартная библиотека Python. Разбор эвристический: ищем подписи полей
(«Начальная цена», «Заказчик», «Обеспечение заявки»…), таблицы позиций и ссылки
на документы. Если площадка поменяет вёрстку, большинство полей всё равно найдётся
по тексту.
"""

import html
import re
import urllib.parse
from html.parser import HTMLParser

SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "head"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SPLIT_CLASS_RE = re.compile(r"(title|info|name|desc|value|label|caption|header|number|price)", re.I)
BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section",
    "article", "header", "footer", "dt", "dd", "dl", "form", "fieldset", "legend", "label",
    "nav", "aside", "main", "blockquote", "pre", "hr",
}


def norm(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


class Page(HTMLParser):
    """Превращает HTML в строки текста, таблицы (строки ячеек) и ссылки."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.cur = [], []
        self.tables, self.tstack = [], []
        self.links, self.a = [], None
        self.skip = 0
        self.title, self.in_title = "", False
        self.h1, self.in_h1 = "", False
        self.open = []  # (tag, split) — элементы с классами-подписями дают отдельные строки

    def _in_cell(self):
        return bool(self.tstack) and self.tstack[-1]["cell"] is not None

    def flush(self):
        s = norm(" ".join(self.cur))
        self.cur = []
        if s:
            self.lines.append(s)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag == "h1" and not self.h1:
            self.in_h1 = True
        if tag in SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        a = dict(attrs)
        if tag not in VOID_TAGS:
            split = bool(SPLIT_CLASS_RE.search(a.get("class") or "")) and tag not in ("td", "th", "tr", "table", "a")
            self.open.append((tag, split))
            if split and not self._in_cell():
                self.flush()
        if tag == "table":
            if not self._in_cell():
                self.flush()
            self.tstack.append({"rows": [], "row": None, "cell": None})
        elif tag == "tr" and self.tstack:
            self.tstack[-1]["row"] = []
        elif tag in ("td", "th") and self.tstack:
            t = self.tstack[-1]
            if t["row"] is None:
                t["row"] = []
            t["cell"] = []
        elif tag == "a":
            self.a = {"href": a.get("href") or "", "title": a.get("title") or "", "text": []}
        if tag in BLOCK_TAGS:
            if self._in_cell():
                self.tstack[-1]["cell"].append(" ")
            else:
                self.flush()

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "h1":
            self.in_h1 = False
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        for k in range(len(self.open) - 1, -1, -1):
            if self.open[k][0] == tag:
                popped = self.open[k:]
                del self.open[k:]
                if any(sp for _, sp in popped) and not self._in_cell():
                    self.flush()
                break
        if tag == "a" and self.a is not None:
            self.links.append({"href": self.a["href"], "text": norm(" ".join(self.a["text"])), "title": norm(self.a["title"])})
            self.a = None
        if tag in ("td", "th") and self.tstack and self.tstack[-1]["cell"] is not None:
            t = self.tstack[-1]
            t["row"].append(norm(" ".join(t["cell"])))
            t["cell"] = None
        elif tag == "tr" and self.tstack and self.tstack[-1]["row"] is not None:
            t = self.tstack[-1]
            row, t["row"] = t["row"], None
            if any(row):
                t["rows"].append(row)
                if len(self.tstack) == 1:
                    self.lines.append(" | ".join(row))
        elif tag == "table" and self.tstack:
            t = self.tstack.pop()
            self.tables.append(t["rows"])
            # Вложенная таблица: её текст становится частью внешней ячейки.
            if self.tstack and self.tstack[-1]["cell"] is not None:
                self.tstack[-1]["cell"].append(" ".join(" ".join(r) for r in t["rows"]))
        elif tag in BLOCK_TAGS and not self._in_cell():
            self.flush()

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.in_h1 and not self.skip:
            self.h1 += data
        if self.skip:
            return
        if self.a is not None:
            self.a["text"].append(data)
        if self._in_cell():
            self.tstack[-1]["cell"].append(data)
        else:
            self.cur.append(data)

    def close(self):
        super().close()
        while self.tstack:
            self.tables.append(self.tstack.pop()["rows"])
        self.flush()


def parse_html(text):
    p = Page()
    try:
        p.feed(text)
        p.close()
    except Exception:  # битая вёрстка: берём то, что успели разобрать
        p.flush()
    return p


# ---------------------------------------------------------------- числа

MONEY_RE = re.compile(r"(\d{1,3}(?:[\s  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)")
PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def to_num(s):
    if s is None:
        return None
    s = str(s)
    m = MONEY_RE.search(s)
    if not m:
        return None
    raw = re.sub(r"[\s  ]", "", m.group(1)).replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def to_pct(s):
    m = PCT_RE.search(s or "")
    return float(m.group(1).replace(",", ".")) if m else None


NONE_RE = re.compile(r"(не\s*предусмотрен|не\s*требует|не\s*установлен|отсутству|^\s*[-—–]\s*$|^нет$)", re.I)


# ---------------------------------------------------------------- поля

FIELDS = {
    "nmck": [
        r"начальн\w*\s*\(?максимальн\w*\)?\s*цен\w*(\s*контракт\w*|\s*договор\w*)?",
        r"\bнмцк?\b",
        r"начальн\w* цен\w*",
        r"максимальн\w* значени\w* цен\w*",
    ],
    "subject": [
        r"наименование объекта закупки",
        r"наименование закупки",
        r"предмет (закупки|договора|контракта)",
        r"наименование процедуры",
        r"наименование лота",
    ],
    "customer": [
        r"^(полное )?наименование заказчика",
        r"^заказчик\w*\s*:?$",
        r"^заказчик\w*\s*(\||:)",
        r"^организаци\w*,? осуществляющ\w* размещение",
        r"^название организации",
        r"^организатор\w*( торгов| закупки)?\s*:?$",
    ],
    "deadline": [
        r"дата и время окончания (срока )?подачи заявок",
        r"окончани\w* (срока )?подачи заявок",
        r"при[её]м заявок\s*(до)?",
        r"подать заявку до",
        r"срок подачи заявок",
    ],
    "place": [
        r"место поставки( товара)?(,? выполнения работ\w*)?(,? (или )?оказания услуг\w*)?",
        r"место выполнения( работ\w*)?(,? (или )?оказания услуг\w*)?",
        r"место оказания( услуг\w*)?",
        r"адрес поставки",
        r"место доставки",
    ],
    "appSec": [r"(размер )?обеспечени\w* заявк\w*"],
    "conSec": [r"(размер )?обеспечени\w* исполнени\w*( контракта| договора)?", r"обеспечени\w* договора"],
    "advance": [r"размер аванса", r"\bаванс\w*", r"предоплат\w*", r"условия оплаты"],
    "contractTerm": [r"срок исполнения контракта", r"срок поставки", r"срок выполнения работ", r"срок исполнения договора"],
}


LABELS_RX = re.compile(r"^\s*(?:" + "|".join(p.lstrip("^") for p in sum(FIELDS.values(), [])) + r")", re.I)
HAS_DATE = lambda v: bool(re.search(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{1,2}\s+[а-я]{3,}\.?\s+\d{4}", v.lower()))
HAS_NUM = lambda v: bool(re.search(r"\d", v)) or bool(NONE_RE.search(v))
IS_TEXT = lambda v: len(v) > 2 and not re.fullmatch(r"[\d\s.,:-]+", v)


def find_value(lines, patterns, ok=None):
    for pat in patterns:
        rx = re.compile(pat, re.I)
        for i, line in enumerate(lines):
            low = line.lower()
            m = rx.search(low)
            if not m:
                continue
            if " | " in line:
                cells = line.split(" | ")
                for j, c in enumerate(cells):
                    if rx.search(c.lower()):
                        rest = [x for x in cells[j + 1:] if x.strip()]
                        if rest and (ok is None or ok(rest[0])):
                            return rest[0]
                        break
            tail = line[m.end():].strip()
            colon = tail.startswith(":")
            if colon:
                tail = tail[1:].strip()
            if tail and tail[0] not in ",(" and (colon or not tail[0].islower()) and (ok is None or ok(tail)) and len(line) < 400:
                return tail
            if len(line) <= m.end() + 3 or len(line) < 90:
                for nxt in lines[i + 1:i + 3]:
                    if nxt.strip() and not LABELS_RX.search(nxt.lower()):
                        if ok is None or ok(nxt):
                            return nxt
                        break
    return None


def money_value(v):
    if v is None or NONE_RE.search(v):
        return None
    n = to_num(v)
    return n if n and n > 0 else None


def sec_pct(v, nmck):
    """Процент обеспечения: из «1 %», из суммы или 0, если «не предусмотрено»."""
    if v is None:
        return None
    if NONE_RE.search(v):
        return 0.0
    p = to_pct(v)
    if p is not None:
        return p
    amount = to_num(v)
    if amount and nmck:
        return round(amount / nmck * 100, 3)
    return None


# ---------------------------------------------------------------- позиции

def _idx(header, pattern):
    rx = re.compile(pattern, re.I)
    for i, h in enumerate(header):
        if rx.search(h):
            return i
    return None


OKPD_RE = re.compile(r"\b\d{2}\.\d{2}(?:\.\d{1,2}){0,2}(?:\.\d{3})?(?:-\d+)?\s*[–—-]\s*", re.U)


def clean_name(s):
    s = norm(s)
    s = re.sub(r"^\d{1,3}[.)]?\s+(?=[A-Za-zА-Яа-яЁё])", "", s)
    s = OKPD_RE.sub(" ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" .;,")
    # «Щебень Щебень» после удаления кода ОКПД2
    words = s.split(" ")
    half = len(words) // 2
    if half and len(words) % 2 == 0 and words[:half] == words[half:]:
        s = " ".join(words[:half])
    return s


def items_from_tables(tables):
    best = []
    for rows in tables:
        if len(rows) < 2:
            continue
        for hi in range(min(3, len(rows))):
            h = [c.lower() for c in rows[hi]]
            if any(len(c) > 80 for c in h):
                continue
            name_i = _idx(h, r"^наименован")
            if name_i is None:
                name_i = _idx(h, r"^(позици|товар|описани|предмет)")
            qty_i = _idx(h, r"^(кол-?во|количеств|объ[её]м)")
            if name_i is None or qty_i is None or name_i == qty_i:
                continue
            unit_i = _idx(h, r"ед\.?\s*изм|единиц")
            price_i = _idx(h, r"цена\s*(за\s*)?ед|цена единиц|цена,|цена\b")
            info_i = _idx(h, r"дополнительн|характеристик")
            need = max(i for i in (name_i, qty_i, unit_i, price_i, info_i) if i is not None)
            out = []
            for r in rows[hi + 1:]:
                if len(r) <= need:
                    continue
                name = clean_name(r[name_i])
                qty = to_num(r[qty_i])
                if not name or not qty or qty <= 0 or re.fullmatch(r"(итого|всего).*", name.lower()):
                    continue
                if info_i is not None and r[info_i] and r[info_i].lower() not in name.lower():
                    name = f"{name} ({norm(r[info_i])[:140]})"
                out.append({
                    "name": name[:240],
                    "unit": norm(r[unit_i])[:30] if unit_i is not None else "",
                    "qty": qty,
                    "nmckUnit": money_value(r[price_i]) if price_i is not None else None,
                })
            if len(out) > len(best):
                best = out
            break
    return best


def items_from_pairs(lines):
    """Росэлторг и похожие: «Наименование | …», «Количество | …», «Ед. измерения | …»."""
    items, cur = [], None

    def push():
        if cur and cur.get("name") and cur.get("qty"):
            items.append(cur)

    for line in lines:
        if " | " not in line:
            continue
        k, _, v = line.partition(" | ")
        k = k.lower().strip()
        if re.fullmatch(r"наименование( товара| позиции)?", k):
            push()
            cur = {"name": clean_name(v)[:240], "unit": "", "qty": None, "nmckUnit": None}
        elif cur is not None and re.match(r"количеств|кол-?во", k):
            cur["qty"] = to_num(v)
        elif cur is not None and re.match(r"ед\.?\s*изм|единиц", k):
            cur["unit"] = norm(v)[:30]
        elif cur is not None and re.match(r"цена", k):
            cur["nmckUnit"] = money_value(v)
    push()
    return items


# ---------------------------------------------------------------- документы

DOC_EXT_RE = re.compile(r"\.(pdf|docx?|xlsx?|rtf|odt|ods|zip|rar|7z|txt|tiff?|jpe?g|png)(\?|#|$)", re.I)
DOC_PATH_RE = re.compile(r"(download|getfile|get_file|attach|filestore|/files?/|fileid|file_id|document/get|/docs?/)", re.I)
FILE_NAME_RE = re.compile(r"[^\s/\\|*\"<>\[\]();:]{1,80}(?:[ \t][^\s/\\|*\"<>\[\]();:]{1,40}){0,8}\.(?:docx?|pdf|xlsx?|rtf|zip|rar|7z|odt)\b", re.I)


def doc_links(links, base_url):
    out, seen = [], set()
    for l in links:
        href = (l.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        url = urllib.parse.urljoin(base_url, href)
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        name = l.get("title") or l.get("text") or ""
        if not (DOC_EXT_RE.search(url) or DOC_PATH_RE.search(url) or FILE_NAME_RE.search(name)):
            continue
        if re.search(r"/(search|region|organizator|help|faq|news)/", url, re.I):
            continue
        if re.search(r"персональн|политик\w* конфиденц|согласи[ея] на|оферт|регламент|тариф|руководств|инструкци\w* пользоват|cookie", (name + " " + url).lower()):
            continue
        if not name:
            name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]) or "Документ"
        seen.add(url)
        out.append({"name": name[:200], "url": url})
    return out[:30]


def mentioned_files(lines):
    names = []
    for line in lines:
        for m in FILE_NAME_RE.finditer(line):
            n = m.group(0).strip()
            if n not in names:
                names.append(n)
    return names[:30]


# ---------------------------------------------------------------- итог

def detect_law(url, text):
    u = url.lower()
    if re.search(r"notice223|223fz|/223/", u):
        return "223"
    if re.search(r"(ea20|ea44|ok20|zk20|ep44|44fz|zp44|/44/)", u):
        return "44"
    if "223-фз" in text.lower():
        return "223"
    if "44-фз" in text.lower():
        return "44"
    return "commercial"


def detect_number(url, lines):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    for key in ("regNumber", "noticeInfoId", "id"):
        if q.get(key):
            return q[key][0]
    m = re.search(r"/procedure/([A-Za-z0-9-]+)", url) or re.search(r"/number/([A-Za-z0-9-]+)", url)
    if m:
        return m.group(1)
    for line in lines[:80]:
        m = re.search(r"(?:№|номер[^\d]{0,20})\s*([0-9A-Za-zА-Яа-я-]{6,30})", line)
        if m:
            return m.group(1)
    return ""


def extract(pages, url):
    """pages: список (url, html). Возвращает словарь с полями закупки, позициями и документами."""
    lines, tables, links, title = [], [], [], ""
    for purl, text in pages:
        p = parse_html(text)
        lines += p.lines
        tables += p.tables
        links += [dict(l, base=purl) for l in p.links]
        title = title or norm(p.title)
    for k, line in enumerate(lines):
        if re.match(r"^\s*#*\s*(похожие закупки|другие закупки|рекомендуем|смотрите также)", line.lower()):
            lines = lines[:k]
            break
    full = "\n".join(lines)

    nmck = money_value(find_value(lines, FIELDS["nmck"], HAS_NUM))
    adv_raw = find_value(lines, FIELDS["advance"], HAS_NUM)
    advance = None
    if adv_raw:
        if re.search(r"предоплат\w*\s*100|100\s*%\s*предоплат|по предоплате 100", adv_raw.lower()):
            advance = 100.0
        else:
            advance = to_pct(adv_raw)
            if advance is None and NONE_RE.search(adv_raw):
                advance = 0.0

    items = items_from_tables(tables)
    if not items:
        items = items_from_pairs(lines)

    docs = []
    for l in links:
        docs += doc_links([l], l["base"])
    uniq, seen = [], set()
    for d in docs:
        if d["url"] not in seen:
            seen.add(d["url"])
            uniq.append(d)
    linked_names = " ".join(d["name"].lower() for d in uniq)
    missing = [n for n in mentioned_files(lines) if n.lower() not in linked_names]

    h1 = ""
    for purl, text in pages[:1]:
        h1 = norm(parse_html(text).h1)
    subject = find_value(lines, FIELDS["subject"], IS_TEXT) or h1 or (max(title.split(" - "), key=len) if title else "")
    return {
        "number": detect_number(url, lines),
        "law": detect_law(url, full),
        "subject": norm(subject)[:300],
        "customer": norm(find_value(lines, FIELDS["customer"], IS_TEXT) or "")[:300],
        "deadline": norm(find_value(lines, FIELDS["deadline"], HAS_DATE) or "")[:80],
        "place": norm(find_value(lines, FIELDS["place"], IS_TEXT) or "")[:300],
        "contractTerm": norm(find_value(lines, FIELDS["contractTerm"], HAS_NUM) or "")[:200],
        "nmck": nmck,
        "appSecPct": sec_pct(find_value(lines, FIELDS["appSec"], HAS_NUM), nmck),
        "conSecPct": sec_pct(find_value(lines, FIELDS["conSec"], HAS_NUM), nmck),
        "advancePct": advance,
        "items": items[:300],
        "docs": uniq[:25],
        "missingDocs": missing[:20],
        "textLength": len(full),
    }


def extra_pages(url):
    """Для ЕИС документы лежат на отдельной вкладке."""
    u = urllib.parse.urlsplit(url)
    if u.hostname and u.hostname.endswith("zakupki.gov.ru") and "common-info" in u.path:
        return [urllib.parse.urlunsplit(u._replace(path=u.path.replace("common-info", "documents")))]
    return []
