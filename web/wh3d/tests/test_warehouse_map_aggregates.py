"""Regresje agregatów widoku mapy 3D (``warehouse_map_detail``):

1. ``hu_by_code`` SUMUJE palety kodów, które po normalizacji są tą samą lokalizacją
   (wcześniej przypisanie → zostawała liczba z ostatniego wariantu zapisu kodu).
2. ``aisle_meta`` kluczuje aleje po (strefa, aleja) — aleja skrzydła prostopadłego
   (rzędy od 0) nie nadpisuje alei bloku głównego o tym samym ``pz``, a ta sama
   numeracja alei w dwóch strefach daje dwie osobne etykiety.
3. Antresola (A0-A3) nie wchodzi do ``aisle_stats`` ani ``cap_groups`` — spójnie z
   renderem, który ją całkowicie pomija.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui import models as m
from wh3d.views import warehouse_map_detail_helpers as h


def _row(snap, code, **kw):
    zone, aisle, stack = code.split("-")
    data = {"zone": zone, "aisle": aisle, "stack": stack[:-1], "col_code": "A",
            "col_idx": 0, "level": 1, "is_empty": False, "capacity_mm": 2000}
    data.update(kw)
    return m.WarehouseSnapshotRow.objects.create(snapshot=snap, location_code=code, **data)


class _ViewCase(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="agg", password="x")
        self.client.post("/login/", {"username": "agg", "password": "x"})
        self.snap = m.WarehouseSnapshot.objects.create(name="Snap", row_count=0)
        self.url = reverse("ui:warehouse_map_detail", args=[self.snap.pk])

    def ctx(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        return r.context


class HuByCodeSumTests(TestCase):
    def _hu(self, seq, location, stock=True):
        ship, _ = m.Shipment.objects.get_or_create(name=f"S-{stock}", is_stock=stock)
        m.HandlingUnit.objects.create(shipment=ship, seq=seq, code=f"H{seq}", location=location)

    def test_variants_of_same_code_are_summed(self):
        self._hu(1, "B0-01-300A")
        self._hu(2, "B0-01-300A")
        self._hu(3, " b0-01-300a ")
        self._hu(4, "b0-01-300A")
        self._hu(5, "B0-02-300A")
        self._hu(6, "B0-01-300A", stock=False)       # nie-magazynowe pomijane
        self.assertEqual(h.hu_by_code(), {"B0-01-300A": 4, "B0-02-300A": 1})


class AisleMetaWingTests(_ViewCase):
    def test_wing_aisle_does_not_swallow_main_aisle(self):
        # Z kodów: B0/01 dostaje pz=0; aleja 55 (90°) po relokacji skrzydła też zaczyna
        # od rzędu 0 → wcześniej pz_aisle[0] trzymał jedną z nich, druga znikała z
        # metadanych, a px_range[0] mieszał zakres bloku głównego i skrzydła.
        _row(self.snap, "C0-04-100A")
        _row(self.snap, "B0-01-300A")
        _row(self.snap, "B0-55-300A")
        lay = m.WarehouseLayout.objects.create(name="Pusty", is_active=True)
        m.WarehouseAisleConfig.objects.create(layout=lay, aisle="55", width_m=2.0, angle_deg=90)
        meta = self.ctx()["aisle_meta"]
        self.assertEqual(sorted((a["zone"], a["aisle"]) for a in meta),
                         [("B0", "01"), ("B0", "55"), ("C0", "04")])
        b01 = next(a for a in meta if a["aisle"] == "01")
        self.assertEqual((b01["px_min"], b01["px_max"]), (0, 0))   # bez px skrzydła

    def test_same_aisle_number_in_two_zones_gets_two_labels(self):
        _row(self.snap, "B0-01-300A")
        _row(self.snap, "C0-01-100A")
        meta = self.ctx()["aisle_meta"]
        self.assertEqual([(a["zone"], a["aisle"], a["label"]) for a in meta],
                         [("B0", "01", "B0-01"), ("C0", "01", "C0-01")])
        self.assertNotEqual(meta[0]["pz_center"], meta[1]["pz_center"])

    def test_unique_aisle_keeps_plain_label(self):
        _row(self.snap, "B0-01-300A")
        _row(self.snap, "B0-02-300A")
        self.assertEqual([a["label"] for a in self.ctx()["aisle_meta"]], ["01", "02"])


class AntresolaOutsideRackStatsTests(_ViewCase):
    def test_antresola_not_in_aisle_stats_nor_cap_groups(self):
        _row(self.snap, "B0-01-300A", capacity_mm=2000)
        _row(self.snap, "B0-01-301A", capacity_mm=2000, is_empty=True)
        _row(self.snap, "A0-01-100A", capacity_mm=700)   # antresola, ta sama aleja 01
        _row(self.snap, "A2-07-100A", capacity_mm=0)     # antresola, własna aleja
        ctx = self.ctx()
        self.assertEqual([(a["label"], a["total"], a["occupied"], a["pct"])
                          for a in ctx["aisle_stats"]], [("01", 2, 1, 50)])
        self.assertEqual(ctx["cap_groups"],
                         {"0": 0, "≤700": 0, "≤1200": 0, "≤2000": 2, ">2000": 0})

    def test_aisle_stats_split_by_zone(self):
        _row(self.snap, "B0-01-300A")
        _row(self.snap, "C0-01-100A", is_empty=True)
        stats = self.ctx()["aisle_stats"]
        self.assertEqual([(a["label"], a["pct"]) for a in stats],
                         [("B0-01", 100), ("C0-01", 0)])
