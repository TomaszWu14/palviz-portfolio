"""Regresja: _pk4 chroni przed Postgres DataError (nienumeryczny / przepełnienie int4 pk)."""
from django.test import SimpleTestCase

from transport.views.shipments import _pk4


class Pk4GuardTest(SimpleTestCase):
    def test_valid_int_passes(self):
        self.assertEqual(_pk4("42"), 42)

    def test_empty_and_nonnumeric_become_zero(self):
        for bad in ("", None, "abc", "12x", "  ", "-5", "3.14"):
            self.assertEqual(_pk4(bad), 0, bad)

    def test_int4_overflow_becomes_zero(self):
        # >2^31-1 wywalałby na PostgreSQL „integer out of range" (500); 0 = brak dopasowania.
        self.assertEqual(_pk4("9999999999"), 0)
        self.assertEqual(_pk4(str(2_147_483_648)), 0)
        self.assertEqual(_pk4(str(2_147_483_647)), 2_147_483_647)   # największy poprawny
