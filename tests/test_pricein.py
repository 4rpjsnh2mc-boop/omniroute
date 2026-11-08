import io
import os
import shutil
import sys
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import pricein as PI  # noqa: E402

EXPECTED = {"Щебень гранитный фр. 5-20": ("т", 2100.0), "Арматура А500С 12 мм": ("т", 61500.0), "Песок строительный мытый": ("т", 650.0)}


def fx(name):
    with open(os.path.join(HERE, "fixtures", name), "rb") as f:
        return f.read()


def make_xlsx(rows):
    strings, sheet = [], []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, v in enumerate(row):
            ref = chr(65 + c) + str(r)
            if isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            else:
                strings.append(v)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        sheet.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(sheet)}</sheetData></worksheet>')
    return buf.getvalue()


def make_docx(rows, paragraphs=()):
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    tbl = "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in r) + "</w:tr>" for r in rows) + "</w:tbl>" if rows else ""
    paras = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document {w}><w:body>{paras}{tbl}</w:body></w:document>")
    return buf.getvalue()


class PriceImportTest(unittest.TestCase):
    def check(self, items):
        got = {i["name"]: (i["unit"], i["cost"]) for i in items}
        for name, val in EXPECTED.items():
            self.assertEqual(got.get(name), val, name)

    def test_excel(self):
        data = make_xlsx([["Прайс ООО СтройБаза"], ["Наименование", "Ед. изм.", "Цена, руб.", "Примечание"],
                          ["Щебень гранитный фр. 5-20", "т", 2100, "карьер"], ["Арматура А500С 12 мм", "т", 61500, ""],
                          ["Песок строительный мытый", "т", "650,00", ""], ["Итого", "", 64250, ""]])
        items, src, _ = PI.parse_bytes("прайс.xlsx", data)
        self.assertEqual(src, "Excel")
        self.check(items)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["note"], "карьер")

    def test_word_table_and_text(self):
        data = make_docx([["Наименование", "Ед.", "Цена"], ["Щебень гранитный фр. 5-20", "т", "2 100,00"],
                          ["Арматура А500С 12 мм", "т", "61 500"], ["Песок строительный мытый", "т", "650 руб."]])
        items, src, _ = PI.parse_bytes("прайс.docx", data)
        self.assertEqual(src, "Word")
        self.check(items)
        data = make_docx([], ["Щебень гранитный фр. 5-20 — т — 2100 руб.", "Арматура А500С 12 мм, т: 61 500,00 ₽",
                              "Песок строительный мытый т 650", "Доставка от 3 000 руб."])
        items, _, _ = PI.parse_bytes("прайс.docx", data)
        self.check(items)
        self.assertEqual(len(items), 3)

    def test_pasted_text(self):
        items = PI.parse_text("Наименование\tЕд.\tЦена\nЩебень гранитный фр. 5-20\tт\t2100\nАрматура А500С 12 мм\tт\t61500\n"
                              "Песок строительный мытый\tт\t650")
        self.check(items)
        items = PI.parse_text("1. Щебень гранитный фр. 5-20 - 2100 р/т\nЦемент М500 мешок 50 кг 520 руб/шт")
        self.assertEqual(items[0]["cost"], 2100.0)
        self.assertEqual(items[1]["cost"], 520.0)

    def test_pdf(self):
        if not shutil.which("pdftotext"):
            self.skipTest("нет pdftotext")
        items, src, _ = PI.parse_bytes("прайс.pdf", fx("price.pdf"))
        self.check(items)
        self.assertEqual(len(items), 6)

    def test_screenshot(self):
        if not shutil.which("tesseract"):
            self.skipTest("нет tesseract")
        items, src, warns = PI.parse_bytes("скрин.png", fx("price.png"))
        self.assertEqual(src, "скриншот")
        self.assertGreaterEqual(len(items), 5)
        got = {i["cost"] for i in items}
        self.assertTrue({2100.0, 650.0, 61500.0} <= got)

    def test_cloud_links(self):
        self.assertEqual(PI.cloud_download_url("https://docs.google.com/spreadsheets/d/AbC-1_x/edit#gid=42"),
                         "https://docs.google.com/spreadsheets/d/AbC-1_x/export?format=csv&gid=42")
        self.assertEqual(PI.cloud_download_url("https://drive.google.com/file/d/XyZ123/view?usp=sharing"),
                         "https://drive.google.com/uc?export=download&id=XyZ123")
        self.assertTrue(PI.is_yandex_disk("https://disk.yandex.ru/i/abcdef"))
        self.assertTrue(PI.is_yandex_disk("https://yadi.sk/d/abcdef"))

    def test_supplier_page_and_archive(self):
        page = ("<html><body><h1>Прайс</h1><table><tr><th>Товар</th><th>Ед</th><th>Цена</th></tr>"
                "<tr><td>Щебень гранитный фр. 5-20</td><td>т</td><td>2 100 ₽</td></tr>"
                "<tr><td>Арматура А500С 12 мм</td><td>т</td><td>61 500</td></tr>"
                "<tr><td>Песок строительный мытый</td><td>т</td><td>650</td></tr></table></body></html>").encode()
        items, src, _ = PI.parse_bytes("price", page)
        self.assertEqual(src, "страница")
        self.check(items)
        items, _, warns = PI.parse_bytes("x", b"<!DOCTYPE html><html><body>Sign in</body></html>")
        self.assertEqual(items, [])
        self.assertIn("доступ", warns[0])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Прайс/а.xlsx", make_xlsx([["Наименование", "Ед.", "Цена"], ["Щебень гранитный фр. 5-20", "т", 2100]]))
            z.writestr("Прайс/б.txt", "Арматура А500С 12 мм\tт\t61500\nПесок строительный мытый\tт\t650\n".encode())
            z.writestr("Прайс/readme.md", "не прайс")
        items, src, _ = PI.parse_bytes("папка.zip", buf.getvalue())
        self.assertEqual(src, "архив")
        self.check(items)

    def test_currency_inside_words(self):
        it = PI.line_to_item("Щебень гранитный фр. 5-20 — т — 2100 руб.")
        self.assertEqual((it["name"], it["unit"], it["cost"]), ("Щебень гранитный фр. 5-20", "т", 2100.0))
        self.assertIsNone(PI.line_to_item("Доставка по Москве — от 3 500 руб."))

    def test_old_formats(self):
        items, src, warns = PI.parse_bytes("old.xls", b"\xd0\xcf\x11\xe0" + b"\0" * 100)
        self.assertEqual(items, [])
        self.assertIn("xlsx", warns[0])


if __name__ == "__main__":
    unittest.main()
