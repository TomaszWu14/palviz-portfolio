"""Status kontroli grupuje strefy per PROCES (decyzja 2026-09-02: 8 stref / 5 procesów).
92EX + WCEX = jedna karta EXPORT (dwuwiersz z kodami stref), a strefy WC* dostają
pigułkę SWOJEGO procesu, nie fallbacku GEIS."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui.models import HandlingUnit, Shipment
from ui.theme import carrier_for


class StatusProcessGroupingTests(TestCase):
    def setUp(self):
        self.lead = get_user_model().objects.create_superuser("lead", "l@l.pl", "x")
        sh = Shipment.objects.create(name="D1")
        mk = lambda seq, wt: HandlingUnit.objects.create(
            shipment=sh, seq=seq, code=f"H{seq}", warehouse_type=wt, status="planned")
        mk(1, "92EX"); mk(2, "WCEX")          # jeden proces EXPORT
        mk(3, "94GL"); mk(4, "WCGL")          # jeden proces GLS
        mk(5, "92JU")                          # BUS solo
        self.client.force_login(self.lead)

    def test_wc_zones_map_to_their_process_not_geis_fallback(self):
        self.assertEqual(carrier_for("WCEX"), "EXPORT")
        self.assertEqual(carrier_for("WCGE"), "GEIS")
        self.assertEqual(carrier_for("WCGL"), "GLS")

    def test_zones_grouped_per_process_with_zone_codes(self):
        zones = self.client.get(reverse("ui:hu_control_status")).context["zones"]
        by_code = {z["code"]: z for z in zones}
        self.assertEqual(set(by_code), {"EXPORT", "GLS", "BUS"})
        self.assertEqual(by_code["EXPORT"]["zone_codes"], "92EX + WCEX")
        self.assertEqual(by_code["GLS"]["zone_codes"], "94GL + WCGL")
        self.assertEqual(by_code["EXPORT"]["planned"], 2)   # obie strefy w jednym liczniku
        self.assertEqual(by_code["BUS"]["zone_codes"], "92JU")
