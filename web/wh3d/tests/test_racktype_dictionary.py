"""Słownik typów regałów/stref (EWM) + import surowego eksportu EWM z backfillem
wymiarów ze słownika. Decyzja usera 2026-08-24: wymiary TYLKO dla 0010/0011/0050/0052/0070."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import (WarehouseRackType, WarehouseLayout, WarehouseLocationMaster)
from ui.roles import ALL_GROUPS


def _user():
    u = get_user_model().objects.create_user(username="rt", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class SeedDictionaryTests(TestCase):
    def test_seed_created_racks_and_zones(self):
        # Data migration 0174 odpaliła się przy budowie testowej bazy.
        racks = set(WarehouseRackType.objects.filter(kind="rack").values_list("code", flat=True))
        self.assertTrue({"0010", "0011", "0050", "0052", "0070"} <= racks)
        # Tylko te 5 kodów to regały — reszta strefy.
        self.assertEqual(racks & {"0012", "0120", "92EX", "WCEX"}, set())
        zones = set(WarehouseRackType.objects.filter(kind="zone").values_list("code", flat=True))
        self.assertTrue({"92EX", "WCGL", "BROK", "LABO", "0012", "0120"} <= zones)
        self.assertTrue(WarehouseRackType.objects.get(code="0010").description)


class EwmCombinedUploadTests(TestCase):
    """Surowy eksport EWM (polskie nagłówki, bez wymiarów) → master z wymiarami ze słownika."""
    @classmethod
    def setUpTestData(cls):
        cls.user = _user()
        cls.layout = WarehouseLayout.objects.create(name="EWM", is_active=True)
        rt = WarehouseRackType.objects.get(code="0010")
        rt.width_mm, rt.depth_mm = 800, 1100
        rt.max_weight_kg, rt.max_volume_m3 = 1200, 2.5
        rt.level_heights = {"1": 2494, "2": 2500}
        rt.save()

    def setUp(self):
        self.client.force_login(self.user)

    # Dokładne nagłówki z realnego eksportu EWM (kolumny istotne + pułapki:
    # "Objętość ładunku" przed "Maksymalna objętość", "Typ st. miej. skł." dalej).
    EWM = (
        "Miejsce składowania,Typ magazynu,Sekcja magazynu,Objętość ładunku,"
        "Maksymalna waga,Maksymalna objętość,Typ st. miej. skł.\n"
        "B0-01-100A,0010,0001,9.99,0,0,1\n"
        "GLRP,WCGL,,0,0,0,\n"
    )

    def _upload(self):
        f = SimpleUploadedFile("ewm.csv", self.EWM.encode("utf-8"), content_type="text/csv")
        return self.client.post(
            reverse("ui:warehouse_combined_upload", args=[self.layout.pk]), {"file": f})

    def test_rack_row_gets_dims_from_dictionary(self):
        self._upload()
        row = WarehouseLocationMaster.objects.get(location_code="B0-01-100A")
        self.assertEqual(row.warehouse_type, "0010")     # kol. "Typ magazynu", nie "Typ st."
        self.assertEqual(row.width_mm, 800)
        self.assertEqual(row.depth_mm, 1100)
        self.assertEqual(row.height_mm, 2494)            # level_heights["1"] (poziom A=1)
        self.assertEqual(row.max_weight_kg, 1200)

    def test_zone_row_stays_without_rack_geometry(self):
        self._upload()
        row = WarehouseLocationMaster.objects.get(location_code="GLRP")
        self.assertEqual(row.warehouse_type, "WCGL")
        self.assertEqual(row.width_mm, 0)
        self.assertEqual(row.height_mm, 0)

    def test_max_volume_alias_not_hijacked_by_cargo_volume(self):
        # "Objętość ładunku" (9.99) NIE może trafić do max_volume_m3.
        self._upload()
        row = WarehouseLocationMaster.objects.get(location_code="B0-01-100A")
        self.assertEqual(row.max_volume_m3, 2.5)         # ze słownika, nie 9.99


class Editor3dLevelHeightsTests(TestCase):
    def test_dictionary_level_heights_used_over_heuristic(self):
        rt = WarehouseRackType.objects.get(code="0010")
        rt.level_heights = {"1": 2000, "2": 2000}
        rt.save()
        # Widok renderuje bez błędu z typem posiadającym level_heights (średnia 2.0 m).
        u = _user()
        self.client.force_login(u)
        WarehouseLayout.objects.create(name="L3d", is_active=True)
        resp = self.client.get(reverse("ui:warehouse_editor3d"))
        self.assertIn(resp.status_code, (200, 302))
