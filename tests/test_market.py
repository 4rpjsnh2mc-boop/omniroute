import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import market as M  # noqa: E402
import tparse as P  # noqa: E402


def fx(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return f.read()


class FakeHeaders(dict):
    def get(self, k, d=None):
        return super().get(k, d)


class MarketTest(unittest.TestCase):
    def test_contract_results(self):
        rows = M.parse_contract_results(fx("eis_contracts.html"))
        self.assertEqual([r["number"] for r in rows], ["2290206036126000101", "3770601234526000045"])
        self.assertIn("contractCard/common-info.html?reestrNumber=2290206036126000101", rows[0]["url"])
        self.assertIn("payment-info-and-target-of-order", M.contract_items_url(rows[0]["url"]))

    def test_card(self):
        c = M.parse_contract_card(fx("eis_card_1.html"), fx("eis_items_1.html"))
        self.assertEqual(c["price"], 252000.0)
        self.assertEqual(c["regionKey"], "арханг")
        self.assertIn("СЕВЕРНЫЙ КАРЬЕР", c["supplier"])
        self.assertEqual(c["items"][0]["nmckUnit"], 2100.0)
        c2 = M.parse_contract_card(fx("eis_card_2.html"), fx("eis_items_2.html"))
        self.assertEqual(c2["regionKey"], "москва")
        self.assertIn("МОССТРОЙМАТЕРИАЛ", c2["supplier"])

    def test_notices(self):
        rows = M.parse_notice_results(fx("eis_notices.html"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["nmck"], 310000.0)
        self.assertIn("отменено", rows[1]["status"])
        self.assertEqual(rows[1]["subject"], "Поставка смазок")

    def test_analyze_end_to_end(self):
        pages = {
            "contract/search": fx("eis_contracts.html"),
            "common-info.html?reestrNumber=2290206036126000101": fx("eis_card_1.html"),
            "payment-info-and-target-of-order.html?reestrNumber=2290206036126000101": fx("eis_items_1.html"),
            "common-info.html?reestrNumber=3770601234526000045": fx("eis_card_2.html"),
            "payment-info-and-target-of-order.html?reestrNumber=3770601234526000045": fx("eis_items_2.html"),
            "extendedsearch": fx("eis_notices.html"),
        }

        def fetch(url):
            for k, v in pages.items():
                if k in url:
                    return url, FakeHeaders(), v.encode()
            raise RuntimeError("unexpected " + url)

        tender = {"items": [{"id": "a", "name": "Щебень (Щебень из плотных горных пород, фракции 5-20)", "unit": "т", "qty": 40}],
                  "place": "Архангельская обл, г Северодвинск", "customer": "", "inn": "2902060361"}
        r = M.analyze(tender, fetch, lambda h, b: b.decode(), pause=0)
        it = r["items"][0]
        self.assertEqual(it["query"], "Щебень")
        self.assertEqual(len(it["matches"]), 2)
        self.assertEqual(it["region"]["median"], 2100.0)
        self.assertEqual(it["rf"]["n"], 2)
        self.assertEqual(it["rf"]["min"], 1900.0)
        self.assertEqual(r["customer"]["canceled"], 1)


class FlagsTest(unittest.TestCase):
    def test_units_and_similarity(self):
        self.assertEqual(P.unit_key("Тонна"), "т")
        self.assertEqual(P.unit_key("Квадратный метр"), "м2")
        self.assertGreater(P.similarity("Щебень гранитный 5-20", "Щебень гранитный фракция 5-20"), 0.6)
        self.assertLess(P.similarity("Щебень 5-20", "Песок строительный"), 0.3)

    def test_flags(self):
        f = P.flags("Товар должен соответствовать ГОСТ. Эквивалент не допускается.")
        self.assertEqual(f["analogs"]["value"], "forbidden")
        f = P.flags("Цемент М500 или эквивалент. Применяется национальный режим.")
        self.assertEqual(f["analogs"]["value"], "allowed")
        self.assertTrue(f["national"]["value"])


if __name__ == "__main__":
    unittest.main()


class DocTextTest(unittest.TestCase):
    def test_docx(self):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", '<w:document><w:body><w:p><w:r><w:t>Цемент М500 или эквивалент</w:t></w:r></w:p>'
                                            '<w:p><w:r><w:t>Только для субъектов малого и среднего предпринимательства</w:t></w:r></w:p></w:body></w:document>')
        text = P.doc_text("ТЗ.docx", buf.getvalue())
        self.assertIn("Цемент М500 или эквивалент", text)
        f = P.flags(text)
        self.assertEqual(f["analogs"]["value"], "allowed")
        self.assertTrue(f["smp"]["value"])
