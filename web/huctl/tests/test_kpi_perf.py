"""KPI kontroli HU bez budowy instancji prób — audyt CODE-009.

kpi_stats liczy panel lidera LIVE (auto-odświeżanie), hub, ekran KPI i raport dzienny.
Pętla po instancjach HUControlAttempt (+ select_related controller/hu) rosła liniowo
kosztem budowy obiektów: pomiar na SQLite — 2 tys. prób ≈ 0,12 s, 20 tys. ≈ 1,4 s (próg
audytu 300 ms). Teraz krotki z values_list; strażnik pilnuje, że pętla nie tworzy modeli,
a liczba zapytań nie zależy od liczby prób."""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from huctl.kpi import kpi_stats
from huctl.models import HUControlAttempt
from ui.models import HandlingUnit, HandlingUnitItem, Shipment


class KpiNoModelInstancesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.users = [U.objects.create_user(f"kpi{i}", password="x") for i in range(3)]
        sh = Shipment.objects.create(name="KPI perf")
        cls.hus = HandlingUnit.objects.bulk_create(
            [HandlingUnit(shipment=sh, seq=i, code=f"KP{i}", warehouse_type="WMS") for i in range(20)])
        cls.items = HandlingUnitItem.objects.bulk_create(
            [HandlingUnitItem(hu=h, ref_code="R", base_unit="OP", base_qty=10) for h in cls.hus])

    def _attempts(self, n):
        HUControlAttempt.objects.bulk_create([
            HUControlAttempt(hu=self.items[i % 20].hu, item=self.items[i % 20],
                             controller=self.users[i % 3], counted_qty=10, counted_unit="OP",
                             result="error" if i % 7 == 0 else "ok", seconds_since_prev=20.0,
                             input_source="keyboard" if i % 5 == 0 else "scan")
            for i in range(n)])

    def _window(self):
        now = timezone.now()
        return now - timedelta(hours=1), now + timedelta(hours=1)

    def test_loop_builds_no_attempt_instances(self):
        self._attempts(60)
        real_init = HUControlAttempt.__init__
        with mock.patch.object(HUControlAttempt, "__init__", autospec=True,
                               side_effect=real_init) as init:
            rows, totals = kpi_stats(*self._window())
            kpi_stats(*self._window(), by="zone")
        self.assertEqual(init.call_count, 0)
        self.assertEqual(sum(r["positions"] for r in rows), 60)
        self.assertEqual(totals["positions"], 20)                 # unikalne pozycje
        self.assertEqual(sum(r["keyboard"] for r in rows), 12)
        self.assertEqual(totals["errors"], 9)

    def test_query_count_does_not_grow_with_attempts(self):
        counts = []
        for n in (5, 120):
            HUControlAttempt.objects.all().delete()
            self._attempts(n)
            with CaptureQueriesContext(connection) as ctx:
                kpi_stats(*self._window())
            counts.append(len(ctx.captured_queries))
        self.assertEqual(counts[0], counts[1], counts)
