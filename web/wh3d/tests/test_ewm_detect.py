"""„Wykryj z EWM”: propozycja szablonów, numeracji i wyjątków z kodów; round-trip = te same kody."""
from collections import defaultdict
from types import SimpleNamespace as NS

from django.test import SimpleTestCase

from wh3d.addressing import expand_model
from wh3d.ewm_detect import detect
from wh3d.tests.ewm_sample import SAMPLE_N_BAYS, load_sample


def master_of(pairs):
    return [(code, typ, 0, 0) for code, typ in pairs]


def rows_of(n_bays):
    return [{"zone": "B0", "rack_id": a, "n_bays": n} for a, n in n_bays.items()]


def expand_proposal(prop, n_bays, width_cm=280):
    """Propozycja → obiekty jak z bazy → rozwinięte kody per przejście."""
    tpl = {t["key"]: NS(pallets_per_beam=t["pallets_per_beam"], levels=t["levels"]) for t in prop["templates"]}
    triples = []
    for r in prop["rows"]:
        row = NS(zone=r["zone"], rack_id=r["rack_id"], n_bays=n_bays[r["rack_id"]], bay_width_cm=width_cm,
                 bay_numbers=r["bay_numbers"], reverse=False)
        ovs = [NS(template=tpl.get(o.get("template")), **{k: v for k, v in o.items() if k != "template"})
               for o in r["overrides"]]
        triples.append((row, tpl[r["template"]], ovs))
    locs, dups = expand_model(triples)
    by_aisle = defaultdict(set)
    for loc in locs:
        by_aisle[loc["aisle"]].add(loc["code"])
    return by_aisle, dups


class DetectRealSampleTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pairs = load_sample()
        cls.prop = detect(rows_of(SAMPLE_N_BAYS), master_of(cls.pairs))
        cls.rows = {r["rack_id"]: r for r in cls.prop["rows"]}
        cls.names = {t["key"]: t["name"] for t in cls.prop["templates"]}

    def test_round_trip_reproduces_every_ewm_code(self):
        by_aisle, dups = expand_proposal(self.prop, SAMPLE_N_BAYS)
        ewm = defaultdict(set)
        for code, _ in self.pairs:
            ewm[code[3:5]].add(code)
        self.assertEqual(dups, {})
        for aisle in SAMPLE_N_BAYS:
            self.assertEqual(by_aisle[aisle], ewm[aisle], aisle)

    def test_templates_are_new_and_named_from_letters_and_types(self):
        self.assertEqual(len(self.prop["templates"]), 18)
        self.assertTrue(all(t["pk"] is None for t in self.prop["templates"]))
        self.assertIn("3 pal. · B C½ D½ X Y Z · 0052/0010", self.names.values())
        self.assertIn("4 pal. · Y Z · 0010", self.names.values())       # przejazd nad drogą (gniazdo 29)

    def test_row_defaults_and_numbering(self):
        self.assertEqual(self.names[self.rows["07"]["template"]], "3 pal. · A X Y Z · 0052/0010")
        self.assertEqual(self.rows["07"]["bay_numbers"], "10-48")
        self.assertEqual(self.rows["08"]["bay_numbers"], "10-47,50")    # EWM przeskakuje 48–49
        self.assertEqual(self.rows["38"]["bay_numbers"], "10-28,30-51")  # bez gniazda 29
        self.assertEqual(self.rows["54"]["bay_numbers"], "29-30,33-64")  # bez 31–32 (n_bays = 34)
        self.assertEqual(self.names[self.rows["48"]["template"]], "3 pal. · B C D X Y Z · 0010/0011")

    def test_overrides_are_bay_templates_plus_single_type_exception(self):
        extra = [o for o in self.rows["08"]["overrides"] if o["action"] != "template"]
        self.assertEqual(extra, [{"bay": 22, "position": 1, "letter": "A", "half": 0,
                                  "action": "ewm_type", "value": "0050"}])
        bay_tpl = [o for o in self.rows["07"]["overrides"] if o["action"] == "template"]
        self.assertEqual(len(bay_tpl), 22)
        self.assertTrue(all(o["letter"] == "" and o["template"].startswith("new:") for o in bay_tpl))

    def test_physical_bays_without_codes_become_bay_skips(self):
        prop = detect(rows_of({"54": 36}), master_of(self.pairs))
        row = prop["rows"][0]
        self.assertEqual(row["bay_numbers"], "29-64")
        skips = [o["bay"] for o in row["overrides"] if o["action"] == "skip" and o["letter"] == ""]
        self.assertEqual(skips, [31, 32])
        by_aisle, _ = expand_proposal(prop, {"54": 36})
        self.assertEqual(by_aisle["54"], {c for c, _ in self.pairs if c.startswith("B0-54-")})

    def test_existing_template_is_reused(self):
        levels = [{"letter": "A", "height_mm": 1500, "ewm_type": "0052", "split": False, "max_kg": 0}] + [
            {"letter": L, "height_mm": 1800, "ewm_type": "0010", "split": False, "max_kg": 0} for L in "XYZ"]
        mine = NS(pk=5, name="Mój paletowy", pallets_per_beam=3, levels=levels)
        prop = detect(rows_of({"07": 39}), master_of(self.pairs), [mine])
        self.assertEqual(prop["rows"][0]["template"], "pk:5")
        self.assertEqual(sum(1 for t in prop["templates"] if t["name"] == "3 pal. · A X Y Z · 0052/0010"), 0)

    def test_rows_without_codes_are_reported(self):
        prop = detect(rows_of({"07": 39, "99": 5}), master_of(self.pairs))
        self.assertEqual(prop["missing_rows"], ["B0-99"])


class DetectIrregularBayTests(SimpleTestCase):
    def test_irregular_bay_gets_nearest_template_plus_skip_and_add(self):
        pairs = []
        for bay in (10, 11, 12):
            pairs += [(f"B0-01-{bay}{p}{L}", "0010") for p in range(3) for L in "AX"]
        pairs += [("B0-01-130A", "0010"), ("B0-01-131A", "0010"), ("B0-01-130B", "0010")]
        pairs += [(f"B0-01-13{p}X", "0010") for p in range(3)]
        prop = detect(rows_of({"01": 4}), master_of(pairs))
        row = prop["rows"][0]
        self.assertEqual(len(prop["templates"]), 1)
        loc_ov = sorted((o["action"], o["bay"], o["position"], o["letter"]) for o in row["overrides"])
        self.assertEqual(loc_ov, [("add", 13, 0, "B"), ("skip", 13, 2, "A")])
        by_aisle, _ = expand_proposal(prop, {"01": 4})
        self.assertEqual(by_aisle["01"], {c for c, _ in pairs})


class DetectHeightsTests(SimpleTestCase):
    def test_level_heights_and_weights_are_medians_from_master(self):
        master = []
        for bay, (height, kg) in zip((10, 11, 12), ((1400, 900), (1500, 1000), (1600, 1100))):
            master += [(f"B0-01-{bay}{p}A", "0052", height, kg) for p in range(3)]
            master += [(f"B0-01-{bay}{p}X", "0010", 0, 0) for p in range(3)]
        prop = detect(rows_of({"01": 3}), master)
        self.assertEqual(len(prop["templates"]), 1)
        levels = {lv["letter"]: lv for lv in prop["templates"][0]["levels"]}
        self.assertEqual((levels["A"]["height_mm"], levels["A"]["max_kg"]), (1500, 1000))
        self.assertEqual((levels["X"]["height_mm"], levels["X"]["max_kg"]), (1000, 0))   # brak danych → domyślne
