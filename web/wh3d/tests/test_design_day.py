"""Profil ruchów i dzień projektowy (krok 3): percentyle, dni robocze, dzień reprezentatywny,
godzina szczytu, ABC/XYZ, profil zleceń + agregacja w bazie (doba lokalna) i widok."""
from datetime import date, datetime, timedelta, timezone as dt_tz

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ui.models import MaterialMaster
from wh3d.design_day import build_profile, load_groups, load_inputs, percentile, working_days
from wh3d.models import WarehouseTask, WarehouseTaskBatch

D = [date(2026, 3, 2) + timedelta(days=i) for i in range(21)]     # 3 tygodnie, pon 2.03


def _daily(values):
    return {d: {"outbound": v} for d, v in zip(D, values, strict=False)}


class PercentileTests(SimpleTestCase):
    def test_matches_excel_percentile_inc(self):
        vals = list(range(1, 101))
        self.assertAlmostEqual(percentile(vals, 95), 95.05)
        self.assertAlmostEqual(percentile(vals, 50), 50.5)
        self.assertAlmostEqual(percentile([10, 20], 90), 19.0)

    def test_degenerate(self):
        self.assertEqual(percentile([], 95), 0.0)
        self.assertEqual(percentile([7], 95), 7.0)


class WorkingDaysTests(SimpleTestCase):
    def test_trickle_days_excluded(self):
        daily = _daily([100, 110, 5, 0, 90])      # 5 = niedziela z pojedynczym ruchem, 0 = brak
        self.assertEqual(working_days(daily), [D[0], D[1], D[4]])

    def test_empty(self):
        self.assertEqual(working_days({}), [])
        self.assertIsNone(build_profile({}, {}))


class ProfileTests(SimpleTestCase):
    def setUp(self):
        vals = [400] * 18 + [620, 900, 3]          # 20 dni roboczych + 1 dzień śladowy
        self.daily = _daily(vals)
        for d in D[:20]:
            self.daily[d]["putaway"] = 50
            self.daily[d]["orders"] = 40
        self.hourly = {}
        for d in D[:20]:
            for h in range(6, 14):                  # zmiana 6–14, szczyt o 10:00
                self.hourly[(d, h)] = {"outbound": 80 if h == 10 else 20, "putaway": 5}
        self.p = build_profile(self.daily, self.hourly, p=95)

    def _stream(self, key):
        return next(s for s in self.p["streams"] if s["key"] == key)

    def test_streams_and_design_value_is_percentile_not_mean_or_max(self):
        out = self._stream("outbound")
        self.assertEqual(self.p["days_working"], 20)
        self.assertEqual(out["max"], 900)
        self.assertGreater(out["design"], out["mean"])
        self.assertLess(out["design"], out["max"])
        self.assertEqual(out["design"], round(percentile([400] * 18 + [620, 900], 95), 1))
        self.assertEqual([q for q, _v in out["pcts"]], [90, 95, 99])
        self.assertIn("orders", {s["key"] for s in self.p["streams"]})
        self.assertNotIn("picking", {s["key"] for s in self.p["streams"]})   # brak danych = brak wiersza

    def test_percentile_choice_changes_design(self):
        p90 = build_profile(self.daily, self.hourly, p=90)
        p99 = build_profile(self.daily, self.hourly, p=99)
        d = [next(s["design"] for s in x["streams"] if s["key"] == "total") for x in (p90, self.p, p99)]
        self.assertEqual(d, sorted(d))
        self.assertLess(d[0], d[2])

    def test_design_day_is_real_day_nearest_percentile(self):
        dd = self.p["design_day"]
        self.assertIn(dd["date"], D[:20])
        self.assertEqual(dd["date"], D[18])                 # 620+50 najbliżej P95 sumy
        self.assertEqual(len(dd["hours"]), 24)
        self.assertEqual(dd["hours"][10], 85)

    def test_peak_hour_from_hourly_volumes(self):
        self.assertEqual(self._stream("outbound")["peak_hour"], 80.0)
        self.assertEqual(self.p["hourly_avg"]["outbound"][10], 80.0)
        self.assertEqual(self.p["hourly_avg"]["outbound"][3], 0.0)

    def test_abc_xyz_and_order_profile(self):
        md = {"STALY": {d: 10 for d in D[:20]},                   # co dzień tyle samo → A, X
              "RZADKI": {D[0]: 1, D[9]: 1}}                       # sporadycznie → C, Z
        prof = build_profile(self.daily, self.hourly, md, [1, 1, 3, 4, 12, 30], p=95)
        cells = {(r["abc"], c["xyz"]): c["skus"] for r in prof["abc_xyz"]["rows"] for c in r["cells"]}
        self.assertEqual(cells[("A", "X")], 1)
        self.assertEqual(cells[("C", "Z")], 1)
        self.assertEqual(prof["abc_xyz"]["skus"], 2)
        orders = prof["orders"]
        self.assertEqual([b["orders"] for b in orders["buckets"]], [2, 2, 0, 1, 1])
        self.assertEqual(orders["orders"], 6)


class GroupsTests(TestCase):
    def test_h1_breakdown_by_matnr_then_ref(self):
        self.assertIsNone(load_groups({"1005796": {}}))              # brak importu SAP → brak sekcji
        MaterialMaster.objects.create(matnr="1005796", ref="ZZ-80", h1="03: SPRZĘT")
        MaterialMaster.objects.create(matnr="1000471", ref="ŻELE500", h1="01: OPATRUNKI")
        groups = load_groups({"000000000001005796": {}, "ŻELE500": {}, "OBCY": {}})
        self.assertEqual(groups, {"000000000001005796": "03: SPRZĘT", "ŻELE500": "01: OPATRUNKI", "OBCY": None})
        daily = {d: {"picking": 100} for d in D[:5]}
        md = {"000000000001005796": {D[0]: 30, D[1]: 30}, "ŻELE500": {D[0]: 20}, "OBCY": {D[2]: 20}}
        prof = build_profile(daily, {}, md, p=95, groups=groups)
        self.assertEqual([(g["label"], g["lines"], g["share"]) for g in prof["groups"]],
                         [("03: SPRZĘT", 60, 60.0), ("01: OPATRUNKI", 20, 20.0),
                          ("Bez hierarchii w danych SAP", 20, 20.0)])


class LoadAndViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("dd", password="x")
        cls.user.groups.add(Group.objects.get_or_create(name="Podgląd")[0])  # _planner wymaga roli
        cls.batch = WarehouseTaskBatch.objects.create(name="Marzec", status="done")
        t0 = datetime(2026, 3, 2, 7, 0, tzinfo=dt_tz.utc)          # 08:00 w Warszawie
        rows = [("outbound", "M1", "D1", t0), ("picking", "M1", "D2", t0 + timedelta(minutes=5)),
                ("picking", "M2", "D2", t0 + timedelta(minutes=6)), ("putaway", "", "", t0),
                # 23:30 UTC 2.03 = 00:30 3.03 w Warszawie → liczy się do 3.03
                ("outbound", "M1", "D3", datetime(2026, 3, 2, 23, 30, tzinfo=dt_tz.utc)),
                ("move", "", "", None)]
        for kind, mat, doc, at in rows:
            WarehouseTask.objects.create(batch=cls.batch, kind=kind, material=mat, document=doc, confirmed_at=at)

    def setUp(self):
        cache.clear()

    def test_load_inputs_local_day_orders_lines(self):
        inp = load_inputs(self.batch)
        d2, d3 = date(2026, 3, 2), date(2026, 3, 3)
        self.assertEqual(inp["daily"][d2], {"outbound": 1, "picking": 2, "putaway": 1, "orders": 2})
        self.assertEqual(inp["daily"][d3], {"outbound": 1, "orders": 1})
        self.assertEqual(inp["hourly"][(d2, 8)]["picking"], 2)
        self.assertEqual(inp["material_days"]["M1"], {d2: 2, d3: 1})
        self.assertEqual(sorted(inp["lines_per_order"]), [2])       # D2: 2 linie kompletacji

    def test_view_requires_login_and_done_batch(self):
        url = reverse("ui:ewm_tasks_profile", args=[self.batch.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        queued = WarehouseTaskBatch.objects.create(name="W kolejce")
        self.assertEqual(self.client.get(reverse("ui:ewm_tasks_profile", args=[queued.pk])).status_code, 404)

    def test_view_renders_percentiles_and_charts(self):
        self.client.force_login(self.user)
        url = reverse("ui:ewm_tasks_profile", args=[self.batch.pk])
        r = self.client.get(url, {"p": "99"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["p"], 99)
        self.assertContains(r, 'href="?p=99" aria-current="true"')
        self.assertContains(r, "Linie kompletacji")
        self.assertContains(r, 'id="dd-daily"')
        self.assertContains(r, "percentyl z tak krótkiej historii")      # 2 dni robocze < MIN_DAYS
        self.assertEqual(self.client.get(url, {"p": "42"}).context["p"], 95)   # spoza listy → domyślny

    def test_empty_batch_message(self):
        self.client.force_login(self.user)
        empty = WarehouseTaskBatch.objects.create(name="Pusta", status="done")
        self.assertContains(self.client.get(reverse("ui:ewm_tasks_profile", args=[empty.pk])),
                            "Brak potwierdzonych zadań")
