import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import tparse as P  # noqa: E402


def fx(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return f.read()


class EisTest(unittest.TestCase):
    URL = "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=0373100043226000168"

    def setUp(self):
        docs_url = P.extra_pages(self.URL)[0]
        self.r = P.extract([(self.URL, fx("eis44.html")), (docs_url, fx("eis44_docs.html"))], self.URL)

    def test_fields(self):
        r = self.r
        self.assertEqual(r["number"], "0373100043226000168")
        self.assertEqual(r["law"], "44")
        self.assertEqual(r["subject"], "Поставка строительных материалов для ремонта кровли")
        self.assertIn("ЖИЛИЩНИК", r["customer"])
        self.assertEqual(r["nmck"], 1250000.0)
        self.assertIn("17.11.2026", r["deadline"])
        self.assertIn("Москва", r["place"])
        self.assertAlmostEqual(r["appSecPct"], 1.0)
        self.assertEqual(r["conSecPct"], 5.0)
        self.assertEqual(r["advancePct"], 30.0)

    def test_items(self):
        items = self.r["items"]
        self.assertEqual([i["name"] for i in items], ["Цемент", "Материал рулонный кровельный наплавляемый", "Праймер битумный"])
        self.assertEqual(items[1]["qty"], 1500.0)
        self.assertEqual(items[1]["nmckUnit"], 480.0)
        self.assertEqual(items[0]["unit"], "Тонна")

    def test_docs(self):
        names = [d["name"] for d in self.r["docs"]]
        self.assertEqual(names, ["Описание объекта закупки.docx", "Обоснование НМЦК.xlsx", "Проект контракта.docx"])
        self.assertTrue(all("filestore" in d["url"] for d in self.r["docs"]))

    def test_documents_tab(self):
        self.assertTrue(P.extra_pages(self.URL)[0].endswith("documents.html?regNumber=0373100043226000168"))


class RoseltorgTest(unittest.TestCase):
    URL = "https://www.roseltorg.ru/procedure/B0711261437542"

    def setUp(self):
        self.r = P.extract([(self.URL, fx("roseltorg.html"))], self.URL)

    def test_fields(self):
        r = self.r
        self.assertEqual(r["number"], "B0711261437542")
        self.assertEqual(r["subject"], "индустриальные масла")
        self.assertIn("ЗВЕЗДОЧКА", r["customer"])
        self.assertIsNone(r["nmck"])
        self.assertIn("13.11.26", r["deadline"])
        self.assertIn("Северодвинск", r["place"])
        self.assertEqual(r["appSecPct"], 0.0)
        self.assertEqual(r["advancePct"], 100.0)

    def test_items(self):
        items = self.r["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["qty"], 360.0)
        self.assertEqual(items[1]["unit"], "Килограмм")
        self.assertTrue(items[0]["name"].startswith("масло И-20"))

    def test_no_boilerplate_docs(self):
        self.assertEqual(self.r["docs"], [])


class NumbersTest(unittest.TestCase):
    def test_money(self):
        self.assertEqual(P.to_num("2 433 400,00 ₽"), 2433400.0)
        self.assertEqual(P.to_num("622 002,78"), 622002.78)
        self.assertEqual(P.to_num("1500"), 1500.0)

    def test_security(self):
        self.assertEqual(P.sec_pct("Не предусмотрено", 1000), 0.0)
        self.assertEqual(P.sec_pct("0,5 %", 1000), 0.5)
        self.assertEqual(P.sec_pct("50 000,00 ₽", 1000000), 5.0)


if __name__ == "__main__":
    unittest.main()
