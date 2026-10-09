"""Prognoza wzrostu z historii zadań EWM (plan 2026-10-02, etap 5)."""
import math
import random
from datetime import date, datetime, timedelta
from datetime import timezone as dt_tz

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from wh3d.design_forecast import backtest, fit, forecast, weekly
from wh3d.models_tasks import WarehouseTask, WarehouseTaskBatch

START = date(2025, 1, 6)                          # poniedziałek


def _daily(weeks, growth_year, noise=0.0, base=1000, seed=1):
    """Dni robocze (pn–pt) z wykładniczym wzrostem `growth_year` rocznie; weekendy puste."""
    rng = random.Random(seed)
    weekly_rate = (1 + growth_year) ** (1 / 52)
    out = {}
    for w in range(weeks):
        for d in range(5):
            day = START + timedelta(days=7 * w + d)
            v = base * weekly_rate ** w * (1 + rng.uniform(-noise, noise))
            out[day] = {"putaway": int(v * 0.3), "picking": int(v * 0.7)}
    return out


def _total(rows):
    return next(r for r in rows if r["key"] == "total")


class ForecastTests(SimpleTestCase):
    def test_recovers_known_growth(self):
        total = _total(forecast(_daily(60, 0.20), years=3))
        self.assertAlmostEqual(total["growth_p50_pct"], 20.0, delta=1.0)
        self.assertAlmostEqual(total["mult_p50"], 1.2 ** 3, delta=0.05)
        self.assertTrue(total["reliable"])

    def test_p90_is_above_p50_with_noise(self):
        total = _total(forecast(_daily(40, 0.10, noise=0.15), years=5))
        self.assertGreater(total["mult_p90"], total["mult_p50"])

    def test_flat_history_gives_multiplier_one(self):
        total = _total(forecast(_daily(30, 0.0), years=5))
        self.assertAlmostEqual(total["mult_p50"], 1.0, delta=0.02)

    def test_backtest_small_error_on_clean_trend(self):
        series = weekly(_daily(40, 0.15))
        self.assertLess(backtest(series), 1.0)
        self.assertIsNone(backtest(series[:10]))

    def test_partial_weeks_and_weekends_are_dropped(self):
        daily = _daily(10, 0.0)
        daily[START - timedelta(days=1)] = {"picking": 900}          # niedziela przed 1. tygodniem
        del daily[START + timedelta(days=9 * 7 + 4)]                   # ostatni tydzień bez piątku
        weeks = weekly(daily)
        self.assertEqual(len(weeks), 9)
        self.assertEqual({v for _, v in weeks}, {5000})                 # pełne tygodnie pn–pt po 1000

    def test_short_history_is_flagged(self):
        self.assertFalse(_total(forecast(_daily(6, 0.1), years=2))["reliable"])

    def test_fit_slope_is_log_weekly_rate(self):
        series = [(START.toordinal() + 7 * i, 100 * 1.01 ** i) for i in range(20)]
        slope, _, se = fit(series)
        self.assertAlmostEqual(slope, math.log(1.01), places=6)
        self.assertAlmostEqual(se, 0.0, places=6)


class ForecastViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.batch = WarehouseTaskBatch.objects.create(name="Historia", status="done")
        rows = []
        for day, kinds in _daily(20, 0.3, base=4).items():
            at = datetime(day.year, day.month, day.day, 8, tzinfo=dt_tz.utc)
            for kind, n in kinds.items():
                rows += [WarehouseTask(batch=cls.batch, kind=kind, confirmed_at=at + timedelta(minutes=i))
                         for i in range(n)]
        WarehouseTask.objects.bulk_create(rows)

    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser(username="f", password="x")
        self.client.post("/login/", {"username": "f", "password": "x"})

    def test_page_shows_multiplier_and_links(self):
        r = self.client.get(f"/magazyn/zadania-ewm/{self.batch.pk}/prognoza/", {"years": 2})
        self.assertContains(r, "Mnożnik — horyzont 2 l.")
        self.assertContains(r, f"/magazyn/zadania-ewm/{self.batch.pk}/symulacja/?p=95&amp;mult=")
        self.assertContains(r, f"/magazyn/zadania-ewm/{self.batch.pk}/porownanie/?p=95&amp;mult=")

    def test_profile_links_to_forecast(self):
        r = self.client.get(f"/magazyn/zadania-ewm/{self.batch.pk}/profil/")
        self.assertContains(r, f"/magazyn/zadania-ewm/{self.batch.pk}/prognoza/")
