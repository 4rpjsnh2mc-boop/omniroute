"""Загрузка прайса из чего угодно: Excel, Word, PDF, CSV, текст, скриншот или фото, ссылка на облако.

Только стандартная библиотека Python. Для скриншотов и сканов используется программа
tesseract (распознавание текста), для PDF — pdftotext. Обе ставятся установщиком.
Результат — строки {name, unit, cost, note} для проверки человеком перед добавлением.
"""

import csv
import html
import io
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET

import tparse as P

UNIT_WORDS = (r"шт|штук[аи]?|кг|г|т|тн|тонн[аы]?|л|литр\w*|м2|м²|кв\.?\s?м|м3|м³|куб\.?\s?м|м|п\.?\s?м|пог\.?\s?м|"
              r"упак\w*|уп|компл\w*|к-т|рулон\w*|рул|меш\w*|бочк\w*|ведр\w*|канистр\w*|пач\w*|лист\w*|пар\w*|набор\w*|"
              r"усл\.?\s?ед|ед")
UNIT_RE = re.compile(r"^(?:" + UNIT_WORDS + r")\.?$", re.I)
NUM_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:[   ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?![\w])")
CURRENCY_RE = re.compile(r"\s*(?<![A-Za-zА-Яа-яЁё])(₽|руб(?:лей|\.)?|р\.?|rub)(?![A-Za-zА-Яа-яЁё])\s*", re.I)
SKIP_LINE_RE = re.compile(r"^(итого|всего|итог|ндс|сумма|страница|стр\.|прайс|price|наименование|тел|e-?mail|www\.|http)", re.I)


def to_price(s):
    s = CURRENCY_RE.sub(" ", str(s or "")).strip()
    m = NUM_RE.search(s)
    if not m:
        return None
    try:
        v = float(re.sub(r"[   ]", "", m.group(1)).replace(",", "."))
    except ValueError:
        return None
    return v if v > 0 else None


def clean_text(s):
    return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip(" |;\t-–—")


# ---------------------------------------------------------------- таблицы → строки прайса

def rows_to_items(rows):
    """rows: список списков ячеек. Ищет заголовок; без заголовка угадывает столбцы."""
    rows = [[clean_text(c) for c in r] for r in rows if any(clean_text(c) for c in r)]
    if not rows:
        return []
    idx = None
    start = 0
    for i, r in enumerate(rows[:15]):
        low = [c.lower() for c in r]
        name_i = next((j for j, c in enumerate(low) if re.search(r"наимен|назван|товар|продукц|номенклат|позиц|описан", c)), None)
        price_i = next((j for j, c in enumerate(low) if re.search(r"цен|стоим|руб|прайс|опт", c) and j != name_i), None)
        if name_i is not None and price_i is not None:
            unit_i = next((j for j, c in enumerate(low) if re.search(r"^ед|ед\.|единиц|изм", c)), None)
            note_i = next((j for j, c in enumerate(low) if re.search(r"прим|коммент|постав|артикул|код|фасовк", c) and j not in (name_i, price_i, unit_i)), None)
            idx = (name_i, unit_i, price_i, note_i)
            start = i + 1
            break
    out = []
    for r in rows[start:]:
        if idx:
            name_i, unit_i, price_i, note_i = idx
            get = lambda j: r[j] if j is not None and j < len(r) else ""
            name, unit, cost, note = get(name_i), get(unit_i), to_price(get(price_i)), get(note_i)
            if cost is None:  # цена бывает в соседнем столбце (оптовая/розничная)
                cost = next((to_price(c) for c in r[price_i + 1:] if to_price(c)), None) if price_i is not None else None
        else:
            name, unit, cost, note = guess_row(r)
        name = re.sub(r"^\d{1,4}[.)]?\s+", "", name or "").strip()
        if not name or not cost or len(re.findall(r"[A-Za-zА-Яа-яЁё]", name)) < 3 or SKIP_LINE_RE.match(name):
            continue
        out.append({"name": name[:240], "unit": unit_norm(unit), "cost": cost, "note": (note or "")[:300]})
    return out


def guess_row(cells):
    """Без заголовка: цена — правая ячейка с числом, единица — короткая ячейка-единица, название — самая длинная текстовая."""
    cells = [c for c in cells if c]
    cost, cost_j = None, None
    for j in range(len(cells) - 1, -1, -1):
        v = to_price(cells[j])
        if v and not re.search(r"[A-Za-zА-Яа-яЁё]{3}", CURRENCY_RE.sub("", cells[j])):
            cost, cost_j = v, j
            break
    unit = next((c for c in cells if UNIT_RE.match(c.translate(LATIN_LOOKALIKE))), "")
    texts = [c for j, c in enumerate(cells) if j != cost_j and c != unit and re.search(r"[A-Za-zА-Яа-яЁё]{3}", c)]
    name = max(texts, key=len) if texts else ""
    note = " ".join(t for t in texts if t != name)[:300]
    return name, unit, cost, note


LATIN_LOOKALIKE = str.maketrans("ABCEHKMOPTXYaceopxyT", "АВСЕНКМОРТХУасеорху т".replace(" ", ""))


def unit_norm(u):
    u = clean_text(u).translate(LATIN_LOOKALIKE)
    if not u:
        return ""
    k = P.unit_key(u)
    return k if len(k) <= 8 else u[:20]


LINE_UNIT_TAIL = re.compile(r"[\s,/|]+(" + UNIT_WORDS + r")\.?\s*$", re.I)


def line_to_item(line):
    """Одна строка текста («Цемент М500, мешок 50 кг   мешок   520,00 ₽») → позиция или None."""
    raw = re.sub(r"[|\[\]{}]", "  ", str(line or ""))  # рамки таблицы → границы столбцов
    raw = re.sub(r"^\s*\d{1,4}[.)]?(\s{2,}|\s(?=[A-Za-zА-Яа-яЁё]))", "", raw)  # номер строки
    s = clean_text(raw)
    if len(s) < 5 or SKIP_LINE_RE.match(s):
        return None
    cells = [c.strip() for c in re.split(r"\t|\s{2,}|;", raw) if c.strip()]
    if len(cells) >= 2:
        name, unit, cost, note = guess_row(cells)
        if name and cost:
            name = re.sub(r"^\d{1,4}[.)]?\s+", "", name)
            if not unit:
                m = LINE_UNIT_TAIL.search(name)
                if m and len(name) - len(m.group(0)) >= 5:
                    unit, name = m.group(1), name[:m.start()]
            return {"name": name.strip(" ,;")[:240], "unit": unit_norm(unit), "cost": cost, "note": note}
    s2 = CURRENCY_RE.sub(" ", s).strip()
    nums = list(NUM_RE.finditer(s2))
    if not nums:
        return None
    last = nums[-1]
    cost = to_price(last.group(1))
    head = s2[:last.start()].rstrip(" :=-–—.\t")
    if re.search(r"(^|\s)(от|до|свыше|более|менее|около)$", head, re.I):
        return None  # «доставка от 3 500 руб.» — не позиция прайса
    if s2[last.end():].strip(" .") and not re.fullmatch(r"(за\s+)?(" + UNIT_WORDS + r")\.?", s2[last.end():].strip(" ./"), re.I):
        return None  # после цены идёт ещё текст: скорее всего, это не строка прайса
    unit = ""
    tail_unit = re.fullmatch(r"(?:за\s+)?(" + UNIT_WORDS + r")\.?", s2[last.end():].strip(" ./"), re.I)
    if tail_unit:
        unit = tail_unit.group(1)
    m = LINE_UNIT_TAIL.search(head)
    if m and not unit:
        unit, head = m.group(1), head[:m.start()]
    name = re.sub(r"^\d{1,4}[.)]?\s+", "", head).strip(" ,;:-–—")
    if not cost or len(re.findall(r"[A-Za-zА-Яа-яЁё]", name)) < 3:
        return None
    return {"name": name[:240], "unit": unit_norm(unit), "cost": cost, "note": ""}


def lines_to_items(text):
    out = []
    for line in text.splitlines():
        it = line_to_item(line)
        if it:
            out.append(it)
    return out


# ---------------------------------------------------------------- форматы файлов

def xlsx_rows(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
        sheets = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        rows = []
        for sh in sheets[:5]:
            root = ET.fromstring(z.read(sh))
            for row in root.iter("{%s}row" % ns["m"]):
                cells = {}
                for c in row.findall("m:c", ns):
                    ref = c.get("r", "")
                    col = 0
                    for ch in re.match(r"[A-Z]*", ref).group(0):
                        col = col * 26 + (ord(ch) - 64)
                    t = c.get("t")
                    v = c.find("m:v", ns)
                    if t == "s" and v is not None:
                        val = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter("{%s}t" % ns["m"]))
                    else:
                        val = v.text if v is not None and v.text else ""
                    cells[max(col - 1, len(cells))] = val
                if cells:
                    rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        return rows


def docx_tables_and_text(data):
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    rows, lines = [], []
    body = root.find(w + "body")
    for el in (body if body is not None else []):
        if el.tag == w + "tbl":
            for tr in el.iter(w + "tr"):
                rows.append(["".join(t.text or "" for t in tc.iter(w + "t")) for tc in tr.findall(w + "tc")])
        elif el.tag == w + "p":
            lines.append("".join(t.text or "" if t.tag == w + "t" else "\t" for t in el.iter() if t.tag in (w + "t", w + "tab")))
    return rows, "\n".join(lines)


def decode_text(data):
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def csv_rows(text):
    sample = text[:5000]
    delim = "\t" if "\t" in sample else ";" if sample.count(";") >= sample.count(",") else ","
    return list(csv.reader(io.StringIO(text), delimiter=delim))


def run(cmd, data=None, timeout=90):
    r = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def pdf_text(data):
    if not shutil.which("pdftotext"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        return run(["pdftotext", "-layout", "-enc", "UTF-8", f.name, "-"])


def pdf_ocr(data):
    if not (shutil.which("pdftoppm") and shutil.which("tesseract")):
        return ""
    with tempfile.TemporaryDirectory() as d:
        src = d + "/in.pdf"
        with open(src, "wb") as f:
            f.write(data)
        subprocess.run(["pdftoppm", "-r", "200", "-l", "5", "-png", src, d + "/p"], capture_output=True, timeout=120)
        import glob
        return "\n".join(image_text(open(p, "rb").read()) or "" for p in sorted(glob.glob(d + "/p*.png")))


def _ocr(path):
    # psm 6: таблица как единый блок строк; preserve_interword_spaces сохраняет столбцы
    return run(["tesseract", path, "-", "-l", "rus+eng", "--psm", "6", "-c", "preserve_interword_spaces=1"], timeout=120)


def image_text(data):
    """Текст с картинки. Если есть ImageMagick: увеличиваем мелкие скриншоты и стираем линии таблицы,
    распознаём оба варианта и берём тот, где нашлось больше позиций прайса."""
    if not shutil.which("tesseract"):
        return None
    with tempfile.TemporaryDirectory() as d:
        src = d + "/in"
        with open(src, "wb") as f:
            f.write(data)
        texts = []
        conv = shutil.which("convert") or shutil.which("magick")
        if conv:
            w = run([shutil.which("identify") or conv, "-format", "%w", src + "[0]"], timeout=30) if shutil.which("identify") else ""
            scale = "300%" if (w.strip().isdigit() and int(w) < 1400) else "150%"
            base = [conv, src + "[0]", "-colorspace", "Gray", "-resize", scale]
            try:
                subprocess.run(base + ["-threshold", "60%", "-negate",
                                       "(", "+clone", "-morphology", "Open", "rectangle:120x1", ")", "-compose", "minus_src", "-composite",
                                       "(", "+clone", "-morphology", "Open", "rectangle:1x60", ")", "-compose", "minus_src", "-composite",
                                       "-negate", d + "/nolines.png"], capture_output=True, timeout=60)
                subprocess.run(base + ["-sharpen", "0x1", d + "/plain.png"], capture_output=True, timeout=60)
            except subprocess.TimeoutExpired:
                pass
            for v in ("nolines.png", "plain.png"):
                try:
                    texts.append(_ocr(d + "/" + v))
                except (subprocess.TimeoutExpired, OSError):
                    continue
        if not any(texts):
            texts.append(_ocr(src))
        return max(texts, key=lambda t: len(lines_to_items(t)))


def kind_of(name, data):
    ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
    head = data[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8") or head[:4] in (b"GIF8", b"RIFF") or ext in ("png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff", "heic"):
        return "image"
    if head.startswith(b"PK"):
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return "unknown"
        if "word/document.xml" in names:
            return "docx"
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole"  # старые .xls/.doc
    if re.match(rb"\s*(<!doctype html|<html|<\?xml[^>]*>\s*<html)", data[:300].lower()):
        return "html"
    if ext in ("csv", "txt", "tsv") or not re.search(rb"[\x00-\x08]", data[:2000]):
        return "text"
    return "unknown"


def parse_zip(data):
    """Архив (например, папка с Яндекс.Диска): читаем файлы прайсов внутри."""
    items, warns, total = [], [], 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist()[:200]:
            if info.is_dir() or not re.search(r"\.(xlsx|docx|pdf|csv|txt|png|jpe?g|webp)$", info.filename, re.I):
                continue
            total += info.file_size
            if info.file_size > 25 * 1024 * 1024 or total > 80 * 1024 * 1024:
                warns.append("В архиве слишком большие файлы, прочитаны не все")
                break
            got, _, w = parse_bytes(info.filename, z.read(info), nested=True)
            items += got
            warns += [x for x in w if x not in warns]
            if len(items) > 5000:
                break
    return items, warns


def parse_bytes(name, data, nested=False):
    """→ (items, source, warnings)"""
    kind = kind_of(name, data)
    warns = []
    if kind == "zip" and not nested:
        items, warns = parse_zip(data)
        return items, "архив", warns
    if kind == "xlsx":
        return rows_to_items(xlsx_rows(data)), "Excel", warns
    if kind == "docx":
        rows, text = docx_tables_and_text(data)
        items = rows_to_items(rows) if rows else []
        return items or lines_to_items(text), "Word", warns
    if kind == "html":
        page = P.parse_html(decode_text(data))
        items = max((rows_to_items(t) for t in page.tables), key=len, default=[])
        items = items or lines_to_items("\n".join(page.lines))
        if not items:
            warns.append("По ссылке открылась страница без прайса. Если это облако, откройте доступ «всем, у кого есть ссылка»")
        return items, "страница", warns
    if kind == "text":
        text = decode_text(data)
        items = rows_to_items(csv_rows(text)) if re.search(r"[\t;,]", text[:3000]) else []
        return items or lines_to_items(text), "текст", warns
    if kind == "pdf":
        text = pdf_text(data)
        if text is None:
            return [], "PDF", ["На сервере нет программы для чтения PDF. Запустите установку ещё раз"]
        items = lines_to_items(text)
        if not items:
            ocr = pdf_ocr(data)
            items = lines_to_items(ocr)
            if items:
                warns.append("PDF оказался сканом: текст распознан, проверьте цены")
        return items, "PDF", warns
    if kind == "image":
        text = image_text(data)
        if text is None:
            return [], "картинка", ["На сервере нет программы распознавания текста. Запустите установку ещё раз"]
        return lines_to_items(text), "скриншот", ["Цены распознаны с картинки: проверьте их перед добавлением"]
    if kind == "ole":
        return [], "старый формат", ["Это старый формат .xls или .doc. Откройте файл и сохраните как .xlsx или .docx, затем загрузите снова"]
    return [], "файл", ["Не получилось прочитать файл. Подходят Excel (.xlsx), Word (.docx), PDF, CSV, картинки"]


def parse_text(text):
    items = rows_to_items(csv_rows(text)) if "\t" in text else []
    return items or lines_to_items(text)


# ---------------------------------------------------------------- ссылки на облака

def cloud_download_url(url):
    """Публичная ссылка облака → прямая ссылка на файл (или исходная ссылка)."""
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    m = re.search(r"/spreadsheets/d/([\w-]+)", url)
    if host.endswith("docs.google.com") and m:
        gid = re.search(r"[#&?]gid=(\d+)", url)
        return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")
    m = re.search(r"/document/d/([\w-]+)", url)
    if host.endswith("docs.google.com") and m:
        return f"https://docs.google.com/document/d/{m.group(1)}/export?format=docx"
    m = re.search(r"/file/d/([\w-]+)", url) or re.search(r"[?&]id=([\w-]+)", url)
    if host.endswith("drive.google.com") and m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    if host.endswith("dropbox.com"):
        return re.sub(r"([?&])dl=0", r"\1dl=1", url) if "dl=0" in url else url + ("&" if "?" in url else "?") + "dl=1"
    return url


def is_yandex_disk(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in ("disk.yandex.ru", "yadi.sk", "disk.yandex.com", "disk.360.yandex.ru") or host.endswith(".disk.yandex.ru")


YANDEX_API = "https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key="
