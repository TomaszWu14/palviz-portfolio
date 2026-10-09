import unittest
from palletizer.io.parsing import parse_int, parse_float


class ParsingTests(unittest.TestCase):
    def test_parse_int_accepts_integral(self):
        self.assertEqual(parse_int("12"), 12)
        self.assertEqual(parse_int("12.0"), 12)

    def test_parse_int_rejects_fractional(self):
        with self.assertRaises(ValueError):
            parse_int("12.7")        # was silently truncated to 12

    def test_parse_int_rejects_sentinels_any_case(self):
        for s in ("NaN", "None", "NULL", ""):
            with self.assertRaises(ValueError):
                parse_int(s)

    def test_parse_float_rejects_inf_nan(self):
        for s in ("inf", "NaN", "None"):
            with self.assertRaises(ValueError):
                parse_float(s)
