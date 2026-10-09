"""Podpowiedź jednostki pobrania na ekranie kontroli HU: import logu pobrań SAP
(PickerActivity) wnosi indeks/ilość/JM/partię, a ekran pulsuje właściwy kafel."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import (Shipment, HandlingUnit, HandlingUnitItem,
                       PickerActivity, PickerActivityBatch)
from ui.roles import GROUP_CONTROLLER, GROUP_MASTER_DATA
from huctl.views.hu_control import _annotate_picked_units, _unit_tile_key


def _user(name, group):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


class UnitTileMapping(TestCase):
    def test_maps_sap_codes_to_tiles(self):
        self.assertEqual(_unit_tile_key("KAR"), "kar")
        self.assertEqual(_unit_tile_key("PAL"), "pal")
        self.assertEqual(_unit_tile_key("OPZ"), "opz")
        self.assertEqual(_unit_tile_key("OP"), "base")
        self.assertEqual(_unit_tile_key(""), "base")


class PickedUnitsHelper(TestCase):
    def setUp(self):
        self.sh = Shipment.objects.create(name="D")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="HU1",
                                              status="in_control")
        self.batch = PickerActivityBatch.objects.create(name="b")

    def _pick(self, mat, unit, qty, lot="", loc="L1"):
        # Data WZGLĘDNA — helper filtruje okno 30 dni; zaszyta data psuła testy po miesiącu.
        from datetime import timedelta
        from django.utils import timezone
        PickerActivity.objects.create(batch=self.batch, location_code=loc,
                                      confirmed_at=timezone.now() - timedelta(days=1),
                                      material_code=mat, unit=unit, qty=qty, lot=lot)

    def test_rollup_pulses_picked_unit_and_labels(self):
        # Karton = 20 op.; pobrano 2 KAR + 1 OP na ten sam indeks.
        item = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A1", base_unit="OP")
        self._pick("A1", "KAR", 2)
        self._pick("A1", "OP", 1)
        _annotate_picked_units([item])
        self.assertEqual(item.picked_units, {"kar", "base"})
        # Same jednostki (sortowane malejąco po ilości), BEZ liczb — kontroler ma
        # policzyć samodzielnie, nie potwierdzić podpowiedź.
        self.assertEqual(item.picked_label, "KAR + OP")

    def test_series_split_matches_by_lot(self):
        # Jeden indeks, dwie serie = dwie linie; każda pulsuje swoją jednostkę.
        i1 = HandlingUnitItem.objects.create(hu=self.hu, ref_code="B2", lot="L100", base_unit="OP")
        i2 = HandlingUnitItem.objects.create(hu=self.hu, ref_code="B2", lot="L200", base_unit="OP")
        self._pick("B2", "KAR", 3, lot="L100")
        self._pick("B2", "OP", 5, lot="L200")
        _annotate_picked_units([i1, i2])
        self.assertEqual(i1.picked_units, {"kar"})
        self.assertEqual(i2.picked_units, {"base"})

    def test_no_match_leaves_empty(self):
        item = HandlingUnitItem.objects.create(hu=self.hu, ref_code="ZZZ", base_unit="OP")
        self._pick("A1", "KAR", 2)
        _annotate_picked_units([item])
        self.assertEqual(item.picked_units, set())
        self.assertEqual(item.picked_label, "")


class ImportCarriesDetail(TestCase):
    def test_import_populates_new_fields(self):
        md = _user("md", GROUP_MASTER_DATA)
        self.client.force_login(md)
        csv = ("lokalizacja,data potw,potwierdzone przez,materiał,ilość,jm,partia\n"
               "L1,2026-08-01 08:00,Jan,A1,2,KAR,L100\n").encode("utf-8")
        f = SimpleUploadedFile("picks.csv", csv, content_type="text/csv")
        self.client.post(reverse("ui:heatmap_upload"), {"file": f, "name": "t"})
        pa = PickerActivity.objects.get(material_code="A1")
        self.assertEqual(pa.unit, "KAR")
        self.assertEqual(pa.qty, 2.0)
        self.assertEqual(pa.lot, "L100")

    def test_legacy_file_without_detail_still_imports(self):
        md = _user("md2", GROUP_MASTER_DATA)
        self.client.force_login(md)
        csv = ("lokalizacja,data potw,potwierdzone przez\n"
               "L1,2026-08-01 08:00,Jan\n").encode("utf-8")
        f = SimpleUploadedFile("old.csv", csv, content_type="text/csv")
        self.client.post(reverse("ui:heatmap_upload"), {"file": f, "name": "t"})
        pa = PickerActivity.objects.get(location_code="L1")
        self.assertEqual(pa.material_code, "")
        self.assertIsNone(pa.qty)


class DetailRendersHint(TestCase):
    def test_detail_shows_pulse_and_label(self):
        ctrl = _user("c", GROUP_CONTROLLER)
        sh = Shipment.objects.create(name="D")
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HU9",
                                         status="in_control", controlled_by=ctrl)
        HandlingUnitItem.objects.create(hu=hu, ref_code="A1", base_unit="OP")
        batch = PickerActivityBatch.objects.create(name="b")
        from datetime import timedelta
        from django.utils import timezone
        PickerActivity.objects.create(batch=batch, location_code="L1",
                                      confirmed_at=timezone.now() - timedelta(days=1),
                                      material_code="A1", unit="OP", qty=7)
        self.client.force_login(ctrl)
        r = self.client.get(reverse("ui:hu_control_detail", args=[hu.pk]))
        self.assertEqual(r.status_code, 200)
        b = r.content.decode()
        self.assertIn("utile--hint", b)
        # Podpowiedź mówi W CZYM pobierano; ilość świadomie ukryta, żeby kontroler
        # liczył samodzielnie, a nie potwierdzał gotowej liczby.
        self.assertIn("Pobierano w: OP", b)
        self.assertNotIn("7 OP", b)
