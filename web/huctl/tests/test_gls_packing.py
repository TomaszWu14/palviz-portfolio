"""(A) Menu skanera: user + strefy przy dzwonku. (B) Strefa GLS: obowiązkowe rozliczenie
kartony→paczki przy księgowaniu + raport konsolidacji lidera."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.models import (Shipment, HandlingUnit, HandlingUnitItem, ControllerZone,
                       GlsPackingEntry)
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


def _controller(name="ctrl"):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


def _gls_hu(user, code="G1", wt="GLS"):
    sh = Shipment.objects.create(name="D")
    hu = HandlingUnit.objects.create(shipment=sh, seq=1, code=code, warehouse_type=wt,
                                     status="in_control", controlled_by=user)
    HandlingUnitItem.objects.create(hu=hu, ref_code="A1", base_qty=5, base_unit="OP",
                                    controlled=True, result="ok", counted_qty=5)
    return hu


class MenuShowsUserAndZones(TestCase):
    def test_zones_listed(self):
        u = _controller("piotr")
        ControllerZone.objects.create(user=u, code="GLS")
        ControllerZone.objects.create(user=u, code="EXPORT")
        self.client.force_login(u)
        r = self.client.get(reverse("ui:hu_control_menu"))
        self.assertContains(r, "piotr")
        # Strefy w dropdownie loginu jako osobne chipy (nie złączone przecinkiem).
        self.assertContains(r, "EXPORT")
        self.assertContains(r, "GLS")

    def test_leader_sees_all_zones(self):
        lead = get_user_model().objects.create_user(username="lead", password="x")
        lead.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        self.client.force_login(lead)
        r = self.client.get(reverse("ui:hu_control_menu"))
        self.assertContains(r, "wszystkie strefy")


@override_settings(GLS_ZONE_CODES=["GLS"])
class GlsFinalizeGate(TestCase):
    def setUp(self):
        self.u = _controller()
        self.client.force_login(self.u)

    def test_finalize_blocked_without_fields(self):
        hu = _gls_hu(self.u)
        self.client.post(reverse("ui:hu_control_finalize", args=[hu.pk]))
        hu.refresh_from_db()
        self.assertNotEqual(hu.status, "ok")                 # księgowanie zablokowane
        self.assertEqual(GlsPackingEntry.objects.count(), 0)

    def test_finalize_with_fields_creates_entry(self):
        hu = _gls_hu(self.u, "G2")
        self.client.post(reverse("ui:hu_control_finalize", args=[hu.pk]),
                         {"gls_cartons": "5", "gls_parcels": "2"})
        hu.refresh_from_db()
        self.assertEqual(hu.status, "ok")
        e = GlsPackingEntry.objects.get(hu=hu)
        self.assertEqual((e.cartons, e.parcels), (5, 2))
        self.assertEqual(e.controller, self.u)

    def test_form_shows_gls_fields_only_in_gls_zone(self):
        hu = _gls_hu(self.u, "G3")
        r = self.client.get(reverse("ui:hu_control_detail", args=[hu.pk]))
        self.assertContains(r, "gls_cartons")
        hu2 = _gls_hu(self.u, "G4", wt="0052")
        r2 = self.client.get(reverse("ui:hu_control_detail", args=[hu2.pk]))
        self.assertNotContains(r2, "gls_cartons")

    def test_non_gls_zone_finalizes_without_fields(self):
        hu = _gls_hu(self.u, "G5", wt="0052")
        self.client.post(reverse("ui:hu_control_finalize", args=[hu.pk]))
        hu.refresh_from_db()
        self.assertEqual(hu.status, "ok")
        self.assertEqual(GlsPackingEntry.objects.count(), 0)


@override_settings(GLS_ZONE_CODES=["GLS"])
class GlsReport(TestCase):
    def test_ranking_orders_by_consolidation(self):
        good, worse = _controller("good"), _controller("worse")
        for i, (who, parcels) in enumerate([(good, 2), (worse, 3)]):
            hu = _gls_hu(who, f"R{i}")
            GlsPackingEntry.objects.create(hu=hu, controller=who, cartons=7,
                                           parcels=parcels, volume_m3=1.2)
        lead = get_user_model().objects.create_user(username="lead", password="x")
        lead.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        self.client.force_login(lead)
        r = self.client.get(reverse("ui:gls_packing_report"))
        rows = r.context["rows"]
        self.assertEqual(rows[0]["name"], "good")            # 7/2=3.5 przed 7/3≈2.33
        self.assertEqual(rows[0]["ratio"], 3.5)
        self.assertEqual(rows[1]["ratio"], 2.33)
        self.assertEqual(rows[0]["avg_parcel_m3"], 0.6)      # 1.2 m³ / 2 paczki

    def test_report_leader_only(self):
        self.client.force_login(_controller("c2"))
        r = self.client.get(reverse("ui:gls_packing_report"))
        self.assertNotEqual(r.status_code, 200)


class ScannerSimulator(TestCase):
    """Symulator skanera: dostęp lidera, iframe na moduły, nagłówki pozwalają na
    osadzenie same-origin (X-Frame-Options=SAMEORIGIN, CSP frame-ancestors 'self')."""

    def test_leader_sees_simulator_with_targets(self):
        lead = get_user_model().objects.create_user(username="sim_lead", password="x")
        lead.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        self.client.force_login(lead)
        r = self.client.get(reverse("ui:scanner_simulator"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "sim-frame")
        self.assertContains(r, reverse("ui:hu_control_menu"))
        self.assertContains(r, reverse("ui:phv_home"))
        self.assertContains(r, "MC330L")

    def test_frame_headers_allow_same_origin(self):
        lead = get_user_model().objects.create_user(username="sim_lead2", password="x")
        lead.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        self.client.force_login(lead)
        r = self.client.get(reverse("ui:hu_control_menu"))
        self.assertEqual(r.headers.get("X-Frame-Options", "").upper(), "SAMEORIGIN")
        csp = (r.headers.get("Content-Security-Policy-Report-Only")
               or r.headers.get("Content-Security-Policy", ""))
        self.assertIn("frame-ancestors 'self'", csp)

    def test_controller_blocked(self):
        self.client.force_login(_controller("sim_ctrl"))
        r = self.client.get(reverse("ui:scanner_simulator"))
        self.assertNotEqual(r.status_code, 200)
