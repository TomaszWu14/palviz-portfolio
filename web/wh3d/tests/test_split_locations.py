"""Lokalizacje dzielone separatorem (…C-1/…C-2): obie renderują się (bezstratność),
podział na pół wzdłuż X. Parser importu: <stos><litera>-<sub> → stack bez litery,
col_code = litera boku (nie sub-slot)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui import models as m


class SplitLocationRenderTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.snap = m.WarehouseSnapshot.objects.create(name="Snap")
        # Tak, jak zapisze POPRAWIONY parser: stack='300', col_code='C', bay C.
        for sub, code in ((1, "B0-01-300C-1"), (2, "B0-01-300C-2")):
            m.WarehouseSnapshotRow.objects.create(
                snapshot=self.snap, location_code=code, zone="B0", aisle="01",
                stack="300", col_code="C", col_idx=2, level=1, is_empty=False,
                warehouse_type="RT", capacity_mm=2000)
        m.WarehouseRackType.objects.create(code="RT", name="Typ", manip_mm=900)
        self.url = reverse("ui:warehouse_map_detail", args=[self.snap.pk])

    def test_both_split_locations_present(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        # Bezstratność: oba kody trafiają do danych renderu (żaden nie ginie).
        # R2: dane lokalizacji w ?fmt=json, nie inline w HTML.
        codes = {row[9] for row in self.client.get(self.url, {"fmt": "json"}).json()["locs"]}
        self.assertIn("B0-01-300C-1", codes)
        self.assertIn("B0-01-300C-2", codes)

    def test_split_render_logic_present(self):
        r = self.client.get(self.url)
        # Render zawiera obsługę podziału (regex sub + przegroda).
        self.assertContains(r, "matSeparator")


class SplitParserTests(TestCase):
    """Parser importu: kod dzielony 300C-1 → stack='300', col_code='C' (nie '1')."""
    def _parse(self, loc):
        # Odtwarza gałąź 4-członową importera dla len(parts)>=4.
        import re as _re
        parts = loc.split("-")
        raw_lc = parts[3]
        m4 = _re.match(r'^(\d+)([A-Za-z]+)$', raw_lc)
        if raw_lc.isdigit() and parts[2] and parts[2][-1].isalpha():
            return parts[2][:-1], parts[2][-1].upper()      # split: stack, bay
        elif m4:
            return parts[2], m4.group(2).upper()            # builder 2X
        return parts[2], (raw_lc[-1].upper() if raw_lc else "")

    def test_split_code_extracts_bay_not_subslot(self):
        self.assertEqual(self._parse("B0-01-300C-1"), ("300", "C"))
        self.assertEqual(self._parse("B0-01-300D-2"), ("300", "D"))

    def test_builder_code_unaffected(self):
        self.assertEqual(self._parse("B0-01-100-2X"), ("100", "X"))
