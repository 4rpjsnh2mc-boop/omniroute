import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

import pricein as PI  # noqa: E402
from test_pricein import make_docx  # noqa: E402


class TenderFromScreenshotsTest(unittest.TestCase):
    def test_screenshot(self):
        if not (shutil.which("tesseract") and shutil.which("convert")):
            self.skipTest("нет tesseract или imagemagick")
        with open(os.path.join(HERE, "fixtures", "tender.png"), "rb") as f:
            part = PI.page_of("скриншот.png", f.read())
        info = PI.tender_from_parts([part])
        self.assertEqual(info["nmck"], 1284600.0)
        self.assertEqual(info["inn"], "7712345678")
        self.assertEqual(info["number"], "4471235")
        self.assertIn("Северная Логистика", info["customer"])
        self.assertTrue(info["subject"].startswith("Поставка строительных материалов"))
        self.assertTrue(info["deadline"].startswith("20.11.2026"))
        self.assertEqual(info["appSecPct"], 0.0)
        self.assertEqual(info["flags"]["analogs"]["value"], "forbidden")
        got = {(i["qty"], i["nmckUnit"]) for i in info["items"]}
        self.assertEqual(got, {(400.0, 560.0), (120.0, 2300.0), (90.0, 700.0), (12.0, 64000.0)})
        self.assertIn(("Арматура А500С 12 мм", "т"), {(i["name"], i["unit"]) for i in info["items"]})

    def test_tenderpro_screenshot(self):
        """Тендер Про: подписи столбцами («Цена контракта / Обеспечение…»), подпись срока на двух строках,
        позиция «МАСЛО И-40А … 14300 кг» с единицей после количества, значок у заказчика."""
        if not (shutil.which("tesseract") and shutil.which("convert")):
            self.skipTest("нет tesseract или imagemagick")
        with open(os.path.join(HERE, "fixtures", "tenderpro.png"), "rb") as f:
            info = PI.tender_from_parts([PI.page_of("скриншот.png", f.read())])
        self.assertEqual(info["number"], "1264766")
        self.assertEqual(info["customer"], 'Общество с ограниченной ответственностью "Пикалевский глиноземный завод"')
        self.assertEqual(info["items"], [{"name": "МАСЛО И-40А", "unit": "кг", "qty": 14300.0, "nmckUnit": None}])
        self.assertEqual(info["subject"], "МАСЛО И-40А")
        self.assertTrue(info["deadline"].startswith("10.11.2026 в 14:00"))
        self.assertIn("МСК+4", info["deadline"])
        self.assertEqual(info["contractTerm"], "30.11.2026")
        self.assertIn("Пикалево", info["place"])
        self.assertIsNone(info["nmck"])
        self.assertEqual((info["appSecPct"], info["conSecPct"]), (0.0, 0.0))
        self.assertTrue(info["flags"]["packaging"]["value"])
        self.assertTrue(info["flags"]["rebid"]["value"])

    def test_ocr_rows_with_glued_columns(self):
        text = ("№   Наименование товара   Ед. изм. Количество Цена за ед. Сумма\n"
                "1 Цемент М500 Д0, мешок 50 кг шт   400   560,00   224 000,00\n"
                "2 Щебень гранитный фр. 5-20 T   120   2 300,00   276 000,00\n"
                "3 Сетка кладочная 50х50 мм,\n"
                "   оцинкованная м2   300   95,00   28 500,00\n"
                "Итого: 528 500,00\n"
                "Поставщик обязан ...")
        items = PI.items_from_text(text)
        self.assertEqual([(i["qty"], i["nmckUnit"], i["unit"]) for i in items],
                         [(400.0, 560.0, "шт"), (120.0, 2300.0, "т"), (300.0, 95.0, "м2")])
        self.assertEqual(items[2]["name"], "Сетка кладочная 50х50 мм, оцинкованная")

    def test_word_spec_and_pasted_text(self):
        doc = make_docx([["№", "Наименование товара", "Ед. изм.", "Кол-во"], ["1", "Кирпич облицовочный красный", "шт", "2000"],
                         ["2", "Техноэласт ЭКП, рулон 10 м2", "рулон", "35"]],
                        ["Техническое задание", "Поставка эквивалента не допускается"])
        info = PI.tender_from_parts([PI.page_of("ТЗ.docx", doc)])
        self.assertEqual([(i["name"], i["qty"]) for i in info["items"]], [("Кирпич облицовочный красный", 2000.0), ("Техноэласт ЭКП, рулон 10 м2", 35.0)])
        self.assertEqual(info["flags"]["analogs"]["value"], "forbidden")
        info = PI.tender_from_parts([{"text": "Начальная цена: 950 000,00 руб.\nПриём заявок до 01.12.2026 12:00\nАванс: 30%", "html": "", "rows": ""}])
        self.assertEqual((info["nmck"], info["advancePct"]), (950000.0, 30.0))
        self.assertTrue(info["deadline"].startswith("01.12.2026"))

    def test_ocr_fixes(self):
        self.assertEqual(PI.ocr_fix("UHH 7712345678, Пеcок"), "ИНН 7712345678, Песок")


if __name__ == "__main__":
    unittest.main()
