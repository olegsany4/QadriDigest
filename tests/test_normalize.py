# tests/test_normalize.py
import unittest
from quadridigest.fetch.normalize import clean_text, CleanItem

class TestNormalize(unittest.TestCase):

    def test_strip_utm(self):
        raw = "Смотри: https://example.com/news?id=1&utm_source=telegram&utm_campaign=x"
        res = clean_text(raw)
        self.assertEqual(res.text, "Смотри: https://example.com/news?id=1")
        self.assertEqual(res.lang, "ru")

    def test_more_line_kept_clean(self):
        raw = "Коротко.\nПодробнее: https://site.ru/a/b?utm_medium=social&x=1"
        res = clean_text(raw)
        self.assertEqual(res.text, "Коротко.\nПодробнее: https://site.ru/a/b?x=1")
        self.assertEqual(res.lang, "ru")

    def test_promo_tail_removed(self):
        raw = "Новость дня.\nПодписывайся на наш канал t.me/newsru"
        res = clean_text(raw)
        self.assertEqual(res.text, "Новость дня.")
        self.assertEqual(res.lang, "ru")

    def test_plain_tme_tail_removed(self):
        raw = "Заголовок\nhttps://t.me/somechannel\n"
        res = clean_text(raw)
        self.assertEqual(res.text, "Заголовок")
        self.assertEqual(res.lang, "ru")

    def test_at_channel_tail_removed(self):
        raw = "Update\n@bestchannel"
        res = clean_text(raw)
        self.assertEqual(res.text, "Update")
        self.assertEqual(res.lang, "en")

    def test_quotes_normalized(self):
        raw = "«Привет», — сказал он. “Hello”, she said."
        res = clean_text(raw)
        self.assertIn('"Привет", — сказал он. "Hello", she said.', res.text)
        self.assertEqual(res.lang, "ru")

    def test_spaces_around_punct(self):
        raw = "Тест  ,проверка:пробелы?OK!"
        res = clean_text(raw)
        self.assertEqual(res.text, "Тест, проверка: пробелы? OK!")
        self.assertEqual(res.lang, "ru")

    def test_nbsp_removed(self):
        raw = "Цена\u00A0100\u00A0руб."
        res = clean_text(raw)
        self.assertEqual(res.text, "Цена 100 руб.")
        self.assertEqual(res.lang, "ru")

    def test_lang_en(self):
        raw = "Breaking news: Fed holds rates. Read more at https://example.com?utm_source=tl"
        res = clean_text(raw)
        self.assertTrue(res.text.startswith("Breaking news: Fed holds rates."))
        self.assertEqual(res.lang, "en")

    def test_und_lang(self):
        raw = "12345 !!! --- https://example.com"
        res = clean_text(raw)
        self.assertIn("https://example.com", res.text)
        self.assertEqual(res.lang, "und")

if __name__ == '__main__':
    unittest.main()
