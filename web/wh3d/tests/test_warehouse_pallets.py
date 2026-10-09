"""Magazyn 3D — realne palety (stock HU) na mapie: hu_count per lokalizacja + licznik
umiejscowionych vs poza mapą (kod nie pasuje)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from django.core.files.uploadedfile import SimpleUploadedFile

from ui.models import (Shipment, HandlingUnit, WarehouseSnapshot, WarehouseSnapshotRow)


class WarehouseSapOccupancy(TestCase):
    def test_blank_puste_is_occupied(self):
        # SAP: 'X' = puste, PUSTE POLE = zajęte. Regresja: całość wychodziła pusta.
        u = get_user_model().objects.create_superuser("wm", password="x")
        self.client.force_login(u)
        csv = "miejsce składowania,puste miejsce skład.\nB0-01-100A,\nB0-01-101A,X\n"
        f = SimpleUploadedFile("s.csv", csv.encode("utf-8"), content_type="text/csv")
        resp = self.client.post(reverse("ui:warehouse_map_upload"), {"file": f, "name": "T"})
        self.assertIn(resp.status_code, (301, 302))
        snap = WarehouseSnapshot.objects.latest("id")
        self.assertEqual(snap.row_count, 2)
        self.assertEqual(snap.occupied_count, 1)   # B0-01-100A (blank) = zajęte, 101A (X) = puste


class WarehousePalletStats(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("wh", password="x")
        cls.stock = Shipment.objects.create(name="Stock", is_stock=True)
        # Paleta na regale (B0, umiejscowiona) + poza mapą (kod nie pasuje).
        HandlingUnit.objects.create(shipment=cls.stock, seq=1, code="H1", location="B0-01-100A")
        HandlingUnit.objects.create(shipment=cls.stock, seq=2, code="H2", location="ZZ-99-999Z")
        # non-stock na tej samej lokalizacji — NIE liczony.
        other = Shipment.objects.create(name="Wys", is_stock=False)
        HandlingUnit.objects.create(shipment=other, seq=1, code="H3", location="B0-01-100A")
        snap = WarehouseSnapshot.objects.create(name="S", row_count=1, occupied_count=1)
        # Regał (B0 — jedyna strefa regałowa).
        WarehouseSnapshotRow.objects.create(
            snapshot=snap, location_code="B0-01-100A", zone="B0", aisle="01", stack="100",
            col_code="A", col_idx=0, level=1, is_empty=False, capacity_mm=1200)
        # Antresola (A0) — usuwana całkowicie z mapy 3D.
        WarehouseSnapshotRow.objects.create(
            snapshot=snap, location_code="A0-01-100A", zone="A0", aisle="01", stack="100",
            col_code="A", col_idx=0, level=1, is_empty=False)
        # Nietypowa (blok/zewnętrzna) — kod spoza wzorca regału, NIE ma zniknąć.
        WarehouseSnapshotRow.objects.create(
            snapshot=snap, location_code="04.01", warehouse_type="92EX",
            aisle="04", stack="01", level=1, is_empty=True)
        cls.snap = snap

    def test_antresola_excluded_rack_present(self):
        # A0 (antresola) usunięta; B0 (regał) obecny na mapie.
        self.client.force_login(self.user)
        resp = self.client.get(reverse("ui:warehouse_map_detail", args=[self.snap.pk]),
                               {"fmt": "json"})   # R2: dane w JSON, nie w kontekście
        codes = {l[9] for l in resp.json()["locs"]}
        self.assertIn("B0-01-100A", codes)
        self.assertNotIn("A0-01-100A", codes)   # antresola całkowicie usunięta

    def test_pallet_stats_placed_vs_unplaced(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("ui:warehouse_map_detail", args=[self.snap.pk]))
        self.assertEqual(resp.status_code, 200)
        ps = resp.context["pallet_stats"]
        self.assertEqual(ps["total"], 2)       # H1 + H2 (stock), nie H3 (non-stock)
        self.assertEqual(ps["placed"], 1)      # B0-01-100A pasuje do wiersza mapy
        self.assertEqual(ps["unplaced"], 1)    # ZZ-99-999Z poza mapą

    def test_loc_carries_hu_count(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("ui:warehouse_map_detail", args=[self.snap.pk]),
                               {"fmt": "json"})
        row = next(l for l in resp.json()["locs"] if l[9] == "B0-01-100A")
        self.assertEqual(row[11], 1)           # hu_count na indeksie 11

    def test_nonrack_counted_not_lost(self):
        # Nietypowa lokalizacja (04.01) liczona jako blok/zewn., nie pominięta.
        # A0 (antresola) NIE liczy się do nonrack — jest filtrowana przed statystyką.
        self.client.force_login(self.user)
        resp = self.client.get(reverse("ui:warehouse_map_detail", args=[self.snap.pk]))
        self.assertEqual(resp.context["stats"]["nonrack"], 1)
        codes = {l[9] for l in self.client.get(
            reverse("ui:warehouse_map_detail", args=[self.snap.pk]),
            {"fmt": "json"}).json()["locs"]}
        self.assertIn("04.01", codes)          # obecna na mapie, nie zniknęła
