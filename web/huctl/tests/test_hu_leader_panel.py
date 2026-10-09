"""Panel kierownika z wynikami kontroli: widoczny na dashboardzie modułu „Kontrola HU",
nieobecny w skanerze.

Kontroler przy skanerze ma widzieć pracę do wykonania, a nie wyniki/ranking — te są
narzędziem kierownika i żyją na panelu desktopowym (hu_control_hub / hu_control_kpi).
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import HandlingUnit, HandlingUnitItem, HUControlAttempt, Shipment
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


class LeaderResultsPanelPlacementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.leader = get_user_model().objects.create_user(username="kier", password="x")
        cls.leader.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        cls.ctrl = get_user_model().objects.create_user(username="oper", password="x")
        cls.ctrl.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        cls.sh = Shipment.objects.create(name="D-PANEL")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HUPANEL",
                                             status="in_control")
        cls.hu.items.create(ref_code="R1", base_unit="OP", base_qty=10)

    # ── skaner: bez wyników ──────────────────────────────────────────────────
    def test_scanner_menu_has_no_results_or_ranking_links(self):
        self.client.force_login(self.ctrl)
        html = self.client.get(reverse("ui:hu_control_menu")).content.decode()
        self.assertNotIn(reverse("ui:hu_control_kpi"), html)
        self.assertNotIn(reverse("ui:hu_error_report"), html)

    def test_scanner_menu_keeps_operational_links(self):
        self.client.force_login(self.ctrl)
        html = self.client.get(reverse("ui:hu_control_menu")).content.decode()
        self.assertIn(reverse("ui:hu_control_recheck_list"), html)
        self.assertIn(reverse("ui:hu_quality_issues"), html)

    def test_kpi_screen_is_desktop_chrome_not_scanner(self):
        self.client.force_login(self.leader)
        resp = self.client.get(reverse("ui:hu_control_kpi"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("ui/control/kpi.html", [t.name for t in resp.templates])
        self.assertNotIn("ui/scanner/base.html", [t.name for t in resp.templates])

    # ── dashboard modułu: wyniki dla kierownika ──────────────────────────────
    def test_hub_shows_results_panel_to_leader(self):
        self.client.force_login(self.leader)
        resp = self.client.get(reverse("ui:hu_control_hub"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Panel kierownika")
        self.assertIn("kpi_totals", resp.context)

    def test_hub_hides_results_panel_from_controller(self):
        self.client.force_login(self.ctrl)
        resp = self.client.get(reverse("ui:hu_control_hub"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Panel kierownika")
        self.assertEqual(list(resp.context["kpi_rows"]), [])

    def test_hub_results_reflect_control_work(self):
        self.client.force_login(self.ctrl)
        item = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, item.pk]),
                         {"action": "confirm", "qty_base": "10"})
        self.client.force_login(self.leader)
        ctx = self.client.get(reverse("ui:hu_control_hub"), {"kpi_period": "month"}).context
        self.assertEqual(ctx["kpi_period"], "month")
        self.assertEqual(ctx["kpi_totals"]["positions"], 1)
        self.assertEqual([r["controller"] for r in ctx["kpi_rows"]], ["oper"])

    def test_hub_and_kpi_agree_on_numbers(self):
        """Skrót na dashboardzie i pełny ekran liczą z jednego źródła (_kpi_stats)."""
        self.client.force_login(self.ctrl)
        item = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, item.pk]),
                         {"action": "confirm", "sure": "1", "qty_base": "9"})   # błąd ilości
        self.client.force_login(self.leader)
        hub = self.client.get(reverse("ui:hu_control_hub"), {"kpi_period": "month"}).context
        kpi = self.client.get(reverse("ui:hu_control_kpi"), {"period": "month"}).context
        self.assertEqual(hub["kpi_totals"], kpi["totals"])
        self.assertEqual(hub["kpi_totals"]["errors"], 1)


class LeaderShiftRowsSingleSourceTests(TestCase):
    """Panel LIVE lidera liczy z tego samego _kpi_stats co ekran KPI — bez drugiej,
    rozjeżdżającej się pętli. Regresja: rekontrola tej samej pozycji NIE zawyża
    licznika 'Skontrolowanych pozycji' (distinct, nie liczba prób)."""

    def setUp(self):
        self.lead = get_user_model().objects.create_superuser("kier2", "k@k.pl", "x")
        self.ctrl = get_user_model().objects.create_user("oper2", password="x")
        self.ctrl.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        sh = Shipment.objects.create(name="D-SS")
        self.hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HUSS",
                                              warehouse_type="92EX")
        self.it = HandlingUnitItem.objects.create(hu=self.hu, ref_code="R1", base_qty=5)
        self.client.force_login(self.lead)

    def test_rework_does_not_inflate_shift_position_count(self):
        # Dwie próby na TEJ SAMEJ (hu, item) — oryginał + rekontrola tego samego kontrolera.
        for _ in range(2):
            HUControlAttempt.objects.create(hu=self.hu, item=self.it, controller=self.ctrl,
                                            exp_base_qty=5, seconds_since_prev=30,
                                            input_source="scan")
        rows = self.client.get(reverse("ui:hu_control_leader")).context["shift_rows"]
        row = next(r for r in rows if r["controller"] == "oper2")
        self.assertEqual(row["distinct_positions"], 1)   # DISTINCT pozycja — panel to pokazuje
        self.assertEqual(row["positions"], 2)            # liczba prób (audyt) — nie licznik panelu
