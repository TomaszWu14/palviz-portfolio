"""PERF-006: strażnicy skalowania liczby zapytań na ekranach list Kontroli HU.

Każdy test mierzy ekran przy 1 i przy 8 „wierszach” (po jednym z KAŻDEGO rodzaju danych,
które ekran listuje) i wymaga tej samej liczby zapytań — N+1 w dowolnej sekcji (np. profil
kontrolera czytany w pętli) rośnie liniowo i wychodzi w porównaniu. Hub i kolejka HU mają
własnych strażników (test_hu_hub_queries, test_call_metrics) — tu ich nie dublujemy."""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from huctl.models import GlsPackingEntry, HUControlAttempt, HUErrorInvestigation
from testkit import factories as f
from testkit.queries import QueryScalingMixin
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


class _HUScreens(QueryScalingMixin, TestCase):
    def setUp(self):
        self.leader = f.UserFactory(username="perf_lider", groups=[GROUP_LEADER])
        self.client.force_login(self.leader)
        self.seq = 0

    def _controller(self):
        self.seq += 1
        return f.UserFactory(username=f"perf_kontroler{self.seq}", groups=[GROUP_CONTROLLER])

    def _hu(self, **kw):
        """HU z klientem (sekcje raportów czytają shipment.customer) w innej strefie/lokalizacji."""
        self.seq += 1
        ship = f.ShipmentFactory(customer=f.CustomerFactory())
        kw.setdefault("warehouse_type", f"W{self.seq:03d}")
        return f.HandlingUnitFactory(shipment=ship, code=f"PERF{self.seq:06d}",
                                     location=f"01-{self.seq:02d}-01", **kw)


class LeaderPanelQueryScalingTests(_HUScreens):
    """Panel lidera (`ui:hu_control_leader`) — najcięższy ekran lidera: kontrolerzy z profilem
    (telefon, urządzenie), HU w kontroli, rekontrole, próby dziś, pilne komunikaty bez
    potwierdzenia, wyjątki master daty i wyjaśniania błędów."""

    def _grow(self, n):
        for _ in range(n):
            ctrl = self._controller()
            live = self._hu(status="in_control", controlled_by=ctrl, assigned_to=ctrl,
                            control_started_at=timezone.now())
            item = f.HandlingUnitItemFactory(hu=live, md_exception=True,
                                             md_exception_note="brak EAN w SAP")
            HUControlAttempt.objects.create(hu=live, item=item, controller=ctrl,
                                            counted_qty=5, counted_unit="KAR",
                                            result="error", seconds_since_prev=2,
                                            input_source="keyboard")
            HUErrorInvestigation.objects.create(hu=live, item=item, controller=ctrl,
                                                error_type="missing")
            recheck = self._hu(status="to_recheck", assigned_to=ctrl)
            f.HUStatusEventFactory(hu=recheck, from_status="in_control", to_status="to_recheck")
            self._hu(status="planned")
            f.NotificationFactory(recipient=ctrl, requires_ack=True)

    def test_leader_panel_does_not_grow_with_rows(self):
        _, _, resp = self.assertQueriesFlat(reverse("ui:hu_control_leader"), self._grow)
        # Wiersze naprawdę są na ekranie (inaczej pomiar niczego nie strzeże).
        self.assertEqual(len(resp.context["live"]), 8)
        self.assertEqual(len(resp.context["rechecks"]), 8)
        self.assertEqual(len(resp.context["md_exceptions"]), 8)
        self.assertEqual(len(resp.context["inv_pending"]), 8)
        self.assertEqual(len(resp.context["shift_rows"]), 8)
        self.assertEqual(resp.context["controllers_today"], 8)

    def test_controller_rows_still_carry_profile_data(self):
        # Dane z profilu (telefon) po select_related — ten sam wynik co przy leniwym dostępie.
        ctrl = self._controller()
        ctrl.profile.phone = "+48 600 100 200"
        ctrl.profile.save()
        rows = self.client.get(reverse("ui:hu_control_leader")).context["controller_rows"]
        by_name = {r["username"]: r for r in rows}
        self.assertEqual(by_name[ctrl.username]["phone"], "+48 600 100 200")


class HUReportsQueryScalingTests(_HUScreens):
    """Raporty HU (huctl/views/hu_reports.py): raport błędów, status stref, lista rekontroli,
    KPI kontrolerów (per kontroler i per strefa) i raport konsolidacji GLS."""

    def _grow_errors(self, n):
        for _ in range(n):
            ctrl = self._controller()
            hu = self._hu(status="to_recheck", controlled_by=ctrl, picker=f"picker{self.seq}")
            f.HandlingUnitItemFactory(hu=hu, result="error", controlled_at=timezone.now(),
                                      error_flags={"wrong_assortment": True})

    def test_error_report(self):
        _, _, resp = self.assertQueriesFlat(reverse("ui:hu_error_report"), self._grow_errors)
        self.assertEqual(resp.context["total"], 8)

    def _grow_status(self, n):
        for _ in range(n):
            hu = self._hu(status="planned")
            f.HandlingUnitItemFactory(hu=hu)
            f.HandlingUnitItemFactory(hu=hu, controlled=True)

    def test_status_screen(self):
        _, _, resp = self.assertQueriesFlat(reverse("ui:hu_control_status"), self._grow_status)
        self.assertEqual(resp.context["summary"]["total"], 8)

    def _grow_recheck(self, n):
        for _ in range(n):
            hu = self._hu(status="to_recheck")
            f.HUStatusEventFactory(hu=hu, from_status="in_control", to_status="to_recheck")

    def test_recheck_list(self):
        _, _, resp = self.assertQueriesFlat(reverse("ui:hu_control_recheck_list"),
                                            self._grow_recheck)
        self.assertEqual(len(resp.context["hus"]), 8)

    def _grow_attempts(self, n):
        for _ in range(n):
            ctrl = self._controller()
            hu = self._hu(status="in_control", controlled_by=ctrl)
            item = f.HandlingUnitItemFactory(hu=hu)
            HUControlAttempt.objects.create(hu=hu, item=item, controller=ctrl, counted_qty=3,
                                            counted_unit="KAR", result="ok",
                                            seconds_since_prev=40)

    def test_kpi_by_controller(self):
        _, _, resp = self.assertQueriesFlat(reverse("ui:hu_control_kpi"), self._grow_attempts)
        self.assertEqual(len(resp.context["rows"]), 8)

    def test_kpi_by_zone(self):
        _, _, resp = self.assertQueriesFlat(reverse("ui:hu_control_kpi"), self._grow_attempts,
                                            params={"by": "zone"})
        self.assertEqual(len(resp.context["rows"]), 8)

    def _grow_gls(self, n):
        for _ in range(n):
            ctrl = self._controller()
            GlsPackingEntry.objects.create(hu=self._hu(status="in_control"), controller=ctrl,
                                           cartons=12, parcels=4, volume_m3=0.8)

    def test_gls_packing_report(self):
        _, _, resp = self.assertQueriesFlat(reverse("ui:gls_packing_report"), self._grow_gls)
        self.assertEqual(len(resp.context["rows"]), 8)
        self.assertEqual(len(resp.context["recent"]), 8)
