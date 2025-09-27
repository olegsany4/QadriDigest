# tests/test_sources_validation.py
import unittest
from quadridigest.fetch.sources import is_plausible_username

class TestUsernameValidation(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(is_plausible_username("investfundsru"))
        self.assertTrue(is_plausible_username("123456789"))
        self.assertTrue(is_plausible_username("https://t.me/somechannel"))

    def test_bad(self):
        self.assertFalse(is_plausible_username("ab"))
        self.assertFalse(is_plausible_username("___badname"))
        self.assertFalse(is_plausible_username("bad name"))
        self.assertFalse(is_plausible_username("bad!name"))

if __name__ == "__main__":
    unittest.main()
