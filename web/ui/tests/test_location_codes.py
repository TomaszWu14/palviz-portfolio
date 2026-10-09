"""Pin the canonical location-code suffix→level convention (3D model)
+ wspólny parser FORMATU kodu lokalizacji (ui/location_codes.py).

The warehouse-map import helpers intentionally still use a legacy positional
scheme (documented inline); the 3D editor must use the canonical map.

Parser formatu: jedno źródło prawdy dla skanera PHV (QR + ręczne wpisanie)
i importu lokalizacji. Strażnik driftu: oba wejścia MUSZĄ używać tego samego
obiektu regex.
"""
from django.test import SimpleTestCase

from ui import views
from ui.location_codes import LOCATION_CODE_RE, match_location_code


class CanonicalSuffixLevelTests(SimpleTestCase):
    def test_standard_rack_levels(self):
        self.assertEqual(views.SUFFIX_LEVEL["X"], 1)
        self.assertEqual(views.SUFFIX_LEVEL["Y"], 2)
        self.assertEqual(views.SUFFIX_LEVEL["Z"], 3)

    def test_floor_and_compact(self):
        self.assertEqual(views.SUFFIX_LEVEL["A"], 0)
        self.assertEqual(views.SUFFIX_LEVEL["B"], 0)
        self.assertEqual(views.SUFFIX_LEVEL["W"], 5)

    def test_editor3d_uses_canonical_map(self):
        self.assertIs(views._ED3D_SUFFIX_LEVEL, views.SUFFIX_LEVEL)


class LocationCodeFormatTests(SimpleTestCase):
    def test_valid_codes(self):
        for code in ("B0-01-100A", "B0-07-300C-1", "A1-12-005D", "b0-01-100a"):
            self.assertIsNotNone(match_location_code(code.upper()), code)

    def test_invalid_codes(self):
        for code in ("", "B0-01-100", "B001100A", "B0-01-100A-", "0B-01-100A",
                     "B0-01-100A-X", "B0_01_100A"):
            self.assertIsNone(match_location_code(code), code)

    def test_groups(self):
        m = match_location_code("B0-07-300C-1")
        self.assertEqual(m.groups(), ("B0", "07", "300", "C-1"))

    def test_whitespace_normalized(self):
        self.assertIsNotNone(match_location_code("  B0-01-100A  "))

    def test_both_entry_points_share_the_regex(self):
        """PHV i import lokalizacji parsują TYM SAMYM obiektem — drift niemożliwy."""
        from ui.views.phv import _LOC_RE
        from ui.management.commands.import_locations import CODE_RE
        self.assertIs(_LOC_RE, LOCATION_CODE_RE)
        self.assertIs(CODE_RE, LOCATION_CODE_RE)
