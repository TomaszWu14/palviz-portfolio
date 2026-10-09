"""Antresola (A0-A3) usuwana całkowicie z mapy 3D: filtr rows_list u źródła →
render, statystyki i strefy pomijają antresolę."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui import models as m
from wh3d.views.warehouse_map import _is_antresola, _special_zones, _RACK_RE


class RackOnlyB0Tests(TestCase):
    """Regałami są WYŁĄCZNIE lokalizacje B0- (decyzja użytkownika)."""
    def test_b0_is_rack(self):
        self.assertTrue(_RACK_RE.match("B0-01-300A"))
        self.assertTrue(_RACK_RE.match("B0-01-300C-1"))   # dzielona

    def test_non_b0_not_rack(self):
        self.assertFalse(_RACK_RE.match("C0-01-300A"))
        self.assertFalse(_RACK_RE.match("A0-01-300A"))    # antresola
        self.assertFalse(_RACK_RE.match("ZWROTY-01"))

    def test_non_b0_becomes_special_zone(self):
        rows = [{"location_code": "C0-01-300A", "zone": "C0", "is_empty": False,
                 "blocked_pick": False, "blocked_put": False}]
        zones = _special_zones(rows, {})
        self.assertEqual([z["prefix"] for z in zones], ["C0"])


class IsAntresolaUnitTests(TestCase):
    def test_prefixes(self):
        self.assertTrue(_is_antresola("A0-01-300A"))
        self.assertTrue(_is_antresola("A3-05-100B"))
        self.assertTrue(_is_antresola("", zone="A1"))
        self.assertTrue(_is_antresola("a2-01-100a"))      # case-insensitive

    def test_non_antresola(self):
        self.assertFalse(_is_antresola("B0-01-300A"))
        self.assertFalse(_is_antresola("A4-01-100A"))     # tylko A0-A3
        self.assertFalse(_is_antresola("ZWROTY-01"))


class AntresolaRenderTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.snap = m.WarehouseSnapshot.objects.create(
            name="Snap", row_count=3, occupied_count=3)
        # 2 antresola (A0, A2) + 1 regał (B0)
        for code, zone in (("A0-01-300A", "A0"), ("A2-01-300A", "A2"), ("B0-01-300A", "B0")):
            m.WarehouseSnapshotRow.objects.create(
                snapshot=self.snap, location_code=code, zone=zone, aisle="01",
                stack="300", col_code="A", level=1, is_empty=False,
                warehouse_type="RT", capacity_mm=2000)
        m.WarehouseRackType.objects.create(code="RT", name="Typ", manip_mm=900)
        self.url = reverse("ui:warehouse_map_detail", args=[self.snap.pk])

    def test_antresola_absent_from_render(self):
        # R2: lokalizacje nie siedzą już inline w HTML — dane sprawdzamy w ?fmt=json.
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        data = self.client.get(self.url, {"fmt": "json"}).json()["locs"]
        codes = {row[9] for row in data}              # loc_code = 10. pole rekordu
        self.assertIn("B0-01-300A", codes)            # regał renderowany
        self.assertNotIn("A0-01-300A", codes)         # antresola usunięta
        self.assertNotIn("A2-01-300A", codes)

    def test_stats_exclude_antresola(self):
        r = self.client.get(self.url)
        # total przeliczony z odfiltrowanych wierszy = 1 (tylko B0), nie 3 z agregatu.
        self.assertEqual(r.context["stats"]["total"], 1)
        self.assertEqual(r.context["stats"]["occupied"], 1)
