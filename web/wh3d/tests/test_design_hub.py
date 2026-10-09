"""Zakładka „Projektowanie magazynu” w Magazyn 3D + plik demonstracyjny WT (tools/ewm_demo_tasks.py)."""
import random
import sys
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from wh3d.ewm_tasks import map_columns, missing_required, parse_row
from wh3d.models_tasks import WarehouseTaskBatch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import ewm_demo_tasks as demo  # noqa: E402


class DesignHubTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="h", password="x")
        self.client.post("/login/", {"username": "h", "password": "x"})

    def test_steps_without_import_are_disabled(self):
        r = self.client.get("/magazyn/")
        self.assertContains(r, "Projektowanie magazynu")
        self.assertContains(r, "/magazyn/model/generator/")
        self.assertContains(r, 'aria-disabled="true"', count=5)

    def test_steps_link_latest_import(self):
        b = WarehouseTaskBatch.objects.create(name="Demo", status="done")
        r = self.client.get("/magazyn/")
        for path in ("profil", "kalibracja", "prognoza", "symulacja", "porownanie"):
            self.assertContains(r, f"/magazyn/zadania-ewm/{b.pk}/{path}/")


class DemoFileTests(SimpleTestCase):
    def test_rows_parse_with_real_importer(self):
        cols = map_columns(demo.HEADERS)
        self.assertEqual(missing_required(cols), [])
        rng = random.Random(1)
        mats = [f"1{n:07d}" for n in range(50)]
        rows = demo.rows_for_day(rng, date(2026, 9, 21), 0, 0.02, mats, [1] * 50, [0])
        kinds = {parse_row(r, cols)["kind"] for r in rows}
        self.assertEqual(kinds, {"putaway", "outbound", "picking", "replenishment", "move"})
        times = [parse_row(r, cols)["confirmed_at"].hour for r in rows]
        self.assertTrue(all(5 <= h < 21 for h in times))
