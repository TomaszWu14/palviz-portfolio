"""Wspólne parsery liczb (views/core): tolerancja europejskiego przecinka + default."""
from django.test import SimpleTestCase

from ui.views.core.helpers import _parse_float, _parse_int


class ParseHelpersTest(SimpleTestCase):
    def test_float_comma_and_dot(self):
        self.assertEqual(_parse_float("40,5"), 40.5)
        self.assertEqual(_parse_float("40.5"), 40.5)
        self.assertEqual(_parse_float(3), 3.0)

    def test_float_bad_and_empty_return_default(self):
        self.assertEqual(_parse_float(""), 0.0)
        self.assertEqual(_parse_float(None), 0.0)
        self.assertEqual(_parse_float("abc"), 0.0)
        self.assertEqual(_parse_float("abc", default=-1.0), -1.0)

    def test_int_via_float(self):
        self.assertEqual(_parse_int("3,0"), 3)
        self.assertEqual(_parse_int("3.9"), 3)          # trunc jak int(float(...))
        self.assertEqual(_parse_int("x", default=1), 1)
        self.assertEqual(_parse_int(""), 0)
