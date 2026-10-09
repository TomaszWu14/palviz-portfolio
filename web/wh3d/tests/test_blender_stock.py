"""Palety w lokalizacjach (stan magazynu) w scenie Blendera — wh3d/blender_stock.py."""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ui import models as m
from wh3d.blender_route import _inside
from wh3d.blender_stock import SlotLocator, abc_by_hits, build_pallets, parse_code

RACK = {"id": 1, "zone": "B0", "rack_id": "01", "x": 2.0, "y": 3.0, "angle": 0,
        "width": 8.1, "depth": 1.1, "level_h": 1.8, "n_bays": 3, "n_levels": 3}
TODAY = date(2026, 9, 25)


class ParseCodeTests(SimpleTestCase):
    def test_letter_encodes_column_and_level_like_3d_map(self):
        self.assertEqual(parse_code("B0-01-300A"), ("B0", "01", "300", 0, 1))
        self.assertEqual(parse_code("b0-01-300k"), ("B0", "01", "300", 2, 2))   # legacy K = kol. 2, poz. 2
        self.assertEqual(parse_code("B0-01-100Z"), ("B0", "01", "100", 0, 4))
        # EWM: B/C/D = półki poziomu 1 w JEDNYM stosie (kolumna 0), nie kolumny obok siebie
        self.assertEqual(parse_code("B0-01-300C"), ("B0", "01", "300", 0, 1))

    def test_four_part_builder_code(self):
        self.assertEqual(parse_code("B0-01-100-2X"), ("B0", "01", "100", 0, 2))

    def test_garbage_is_none(self):
        self.assertIsNone(parse_code("NIEZNANA"))
        self.assertIsNone(parse_code(""))


class SlotLocatorTests(SimpleTestCase):
    def test_bays_ranked_and_lanes_inside_rack(self):
        codes = ["B0-01-100X", "B0-01-100K", "B0-01-200A", "B0-01-300X"]
        loc = SlotLocator([RACK], codes)
        s1, s3 = loc.slot("B0-01-100X"), loc.slot("B0-01-300X")
        self.assertLess(s1["x"], s3["x"])                  # bok 100 przed bokiem 300
        self.assertEqual(s3["level"], 2)
        self.assertAlmostEqual(s3["z"], 1.8 + 0.05)
        for code in codes:
            s = loc.slot(code)
            self.assertTrue(_inside((s["x"], s["y"]), RACK, 0.0), code)
        # legacy kolumny (X/J/K) w boku 2,7 m → szerokość palety ≤ 0,8 m i kolumny się nie nakładają
        a, c = loc.slot("B0-01-100X"), loc.slot("B0-01-100K")
        self.assertLessEqual(a["w"], 0.8)
        self.assertGreater(abs(c["x"] - a["x"]), a["w"])

    def test_master_level_overrides_letter(self):
        # B0: litera = poziom (B = 2), a nie kolumna 1 na poziomie 1
        codes = ["B0-01-100A", "B0-01-100B", "B0-01-100C"]
        loc = SlotLocator([RACK], codes, levels={"B0-01-100A": 1, "B0-01-100B": 2, "B0-01-100C": 3})
        a, b, c = (loc.slot(k) for k in codes)
        self.assertEqual((a["level"], b["level"], c["level"]), (1, 2, 3))
        self.assertEqual(a["x"], b["x"])                          # ta sama kolumna boku
        self.assertAlmostEqual(c["z"] - a["z"], 2 * RACK["level_h"])

    def test_halves_split_the_cell_side_by_side(self):
        # Połówki (…C-1 / …C-2 = miejsce 80 cm podzielone na dwa) nie mogą lądować w jednym punkcie.
        codes = ["B0-01-300B", "B0-01-300C-1", "B0-01-300C-2", "B0-01-300D"]
        loc = SlotLocator([RACK], codes)
        c1, c2, whole = loc.slot("B0-01-300C-1"), loc.slot("B0-01-300C-2"), loc.slot("B0-01-300D")
        self.assertEqual((c1["half"], c2["half"]), (1, 2))
        self.assertNotIn("half", whole)
        self.assertLess(c1["w"], whole["w"])                      # połówka węższa niż całe miejsce (~40 cm)
        self.assertGreaterEqual(abs(c2["x"] - c1["x"]), c1["w"])  # obok siebie, bez nakładania
        for s in (c1, c2):
            self.assertTrue(_inside((s["x"], s["y"]), RACK, 0.0))

    def test_shelves_bcd_stack_vertically_in_level_one(self):
        # B/C/D = półki JEDNA NAD DRUGĄ w otworze poziomu 1 (ten sam x, rosnące z), X nad nimi.
        codes = ["B0-01-300B", "B0-01-300C", "B0-01-300D", "B0-01-300X"]
        loc = SlotLocator([RACK], codes)
        b, c, d, x = (loc.slot(k) for k in codes)
        self.assertEqual({b["x"], c["x"], d["x"], x["x"]}, {b["x"]})
        self.assertEqual((b["level"], c["level"], d["level"], x["level"]), (1, 1, 1, 2))
        self.assertLess(b["z"], c["z"])
        self.assertLess(c["z"], d["z"])
        self.assertLess(d["z"], x["z"])
        self.assertAlmostEqual(c["z"] - b["z"], RACK["level_h"] / 3)

    def test_gh_split_x_vertically(self):
        # G/H = miejsce X podzielone pionowo: G niżej, H wyżej, oba na poziomie 2.
        loc = SlotLocator([RACK], ["B0-01-300G", "B0-01-300H"])
        g, h = loc.slot("B0-01-300G"), loc.slot("B0-01-300H")
        self.assertEqual((g["level"], h["level"]), (2, 2))
        self.assertEqual(g["x"], h["x"])
        self.assertAlmostEqual(h["z"] - g["z"], RACK["level_h"] / 2)

    def test_physical_bays_spread_pallet_positions(self):
        # Model z rysunku: 1 FIZYCZNE gniazdo 2,8 m, a kody niosą 3 pozycje paletowe (300/301/302)
        # — palety muszą leżeć obok siebie, nie w środku gniazda jedna na drugiej.
        rack = dict(RACK, n_bays=1, width=2.8)
        codes = ["B0-01-300X", "B0-01-301X", "B0-01-302X"]
        loc = SlotLocator([rack], codes)
        xs = [loc.slot(c)["x"] for c in codes]
        w = loc.slot(codes[0])["w"]
        self.assertEqual(xs, sorted(xs))
        self.assertGreaterEqual(min(b - a for a, b in zip(xs, xs[1:], strict=False)), w)
        for c in codes:
            self.assertTrue(_inside((loc.slot(c)["x"], loc.slot(c)["y"]), rack, 0.0), c)

    def test_unknown_rack_is_none(self):
        self.assertIsNone(SlotLocator([RACK], []).slot("C9-99-100A"))

    def test_level_clamped_to_rack(self):
        s = SlotLocator([RACK], ["B0-01-100Z"]).slot("B0-01-100Z")   # poziom 4 > n_levels 3
        self.assertEqual(s["level"], 3)


class BuildPalletsTests(SimpleTestCase):
    def test_sources_merge_by_location(self):
        snap = [
            {"location_code": "B0-01-100A", "is_empty": False, "blocked_pick": False,
             "blocked_put": False, "capacity_mm": 1500},
            {"location_code": "B0-01-200A", "is_empty": True, "blocked_pick": True,
             "blocked_put": False, "capacity_mm": 0},
            {"location_code": "B0-01-300A", "is_empty": True, "blocked_pick": False,
             "blocked_put": False, "capacity_mm": 0},
            {"location_code": "Z9-99-100A", "is_empty": False, "blocked_pick": False,
             "blocked_put": False, "capacity_mm": 0},
        ]
        stock = [{"location": "B0-01-100A", "sku": "M1", "name": "Rękawice", "lot": "L1",
                  "expiry": TODAY + timedelta(days=20), "qty": 30, "unit": "KAR", "hu": "HU1"}]
        activity = [("B0-01-100A", "M1")] * 9 + [("B0-01-300A", "M2")]
        pallets, stats, _ = build_pallets([RACK], snap, stock, activity, today=TODAY)
        by = {p["code"]: p for p in pallets}
        self.assertEqual(set(by), {"B0-01-100A", "B0-01-200A"})   # puste niezablokowane pomijamy
        p = by["B0-01-100A"]
        self.assertEqual((p["state"], p["sku"], p["abc"], p["picks"]), ("occupied", "M1", "A", 9))
        self.assertEqual(p["days_to_expiry"], 20)
        self.assertAlmostEqual(p["h"], 1.35)                       # 90% z 1500 mm
        self.assertEqual(by["B0-01-200A"]["state"], "blocked_empty")
        self.assertEqual(stats["unmapped"], 1)                     # Z9 nie ma w modelu
        self.assertEqual(stats["occupied"], 1)

    def test_stock_without_snapshot_is_enough(self):
        stock = [{"location": "B0-01-100A", "sku": "M1", "qty": 5, "hu": "HU1"}]
        pallets, stats, _ = build_pallets([RACK], [], stock, [], today=TODAY)
        self.assertEqual(len(pallets), 1)
        self.assertEqual(pallets[0]["h"], 1.2)                     # domyślna wysokość ładunku

    def test_abc_thresholds(self):
        self.assertEqual(abc_by_hits({"a": 80, "b": 15, "c": 5}), {"a": "A", "b": "B", "c": "C"})


class BlenderStockViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("bs", password="x")
        cls.user.groups.add(Group.objects.get_or_create(name="Podgląd")[0])  # _planner wymaga roli
        cls.wm = m.WarehouseModel.objects.create(name="Hala")
        m.WarehouseModelRack.objects.create(model=cls.wm, zone="B0", rack_id="01",
                                            n_bays=2, n_levels=2, x_m=2, y_m=3)
        snap = m.WarehouseSnapshot.objects.create(name="LX03 wrzesień")
        for code, empty in (("B0-01-100A", False), ("B0-01-200X", False), ("B0-01-200A", True)):
            m.WarehouseSnapshotRow.objects.create(snapshot=snap, location_code=code, is_empty=empty,
                                                  capacity_mm=1400)
        stock = m.Shipment.objects.create(name="Stock", is_stock=True)
        hu = m.HandlingUnit.objects.create(shipment=stock, seq=1, code="HU777", location="B0-01-100A")
        m.HandlingUnitItem.objects.create(hu=hu, ref_code="REF1", description="Maseczki",
                                          base_qty=12, base_unit="szt")
        transport = m.Shipment.objects.create(name="Dostawa", is_stock=False)
        other = m.HandlingUnit.objects.create(shipment=transport, seq=1, code="HU9", location="B0-01-200A")
        m.HandlingUnitItem.objects.create(hu=other, ref_code="NIE", base_qty=1)

    def setUp(self):
        self.client.force_login(self.user)

    def _get(self, **params):
        return self.client.get(reverse("ui:warehouse_model_blender_json", args=[self.wm.pk]), params)

    def test_latest_snapshot_plus_hu_stock(self):
        sc = self._get(snapshot="latest", forklifts=0).json()
        by = {p["code"]: p for p in sc["pallets"]}
        self.assertEqual(set(by), {"B0-01-100A", "B0-01-200X"})     # transportowa HU nie jest stanem
        self.assertEqual((by["B0-01-100A"]["sku"], by["B0-01-100A"]["qty"]), ("REF1", 12))
        self.assertEqual(by["B0-01-100A"]["hu"], ["HU777"])
        self.assertEqual(by["B0-01-200X"]["level"], 2)
        self.assertEqual(sc["source"]["snapshot"], "LX03 wrzesień")
        self.assertEqual(sc["stock_stats"]["unmapped"], 0)

    def test_pallets_can_be_disabled(self):
        sc = self._get(pallets="0").json()
        self.assertEqual(sc["pallets"], [])
        self.assertIsNone(sc["stock_stats"])
