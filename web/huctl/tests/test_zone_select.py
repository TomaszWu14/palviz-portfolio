"""Strefy kontroli (przebudowa 2026-09): wybór aktywnej strefy, filtrowanie pracy po
strefie, badge w nagłówku zamiast dzwonka, powiadomienia lustrzane w komunikatorze."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import HandlingUnit, Shipment, MessageThread
from ui.roles import GROUP_CONTROLLER


def _controller_user(username, allowed="", section=""):
    u = get_user_model().objects.create_user(username=username, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    u.profile.allowed_sections = allowed
    u.profile.section = section
    u.profile.save(update_fields=["allowed_sections", "section"])
    return u


class ZoneSelectFlowTests(TestCase):
    def test_menu_redirects_to_zone_select_when_no_active_zone(self):
        u = _controller_user("multi", allowed="BUS,EXPORT")
        self.client.force_login(u)
        resp = self.client.get(reverse("ui:hu_control_menu"))
        self.assertRedirects(resp, reverse("ui:hu_zone_select"))

    def test_single_zone_is_auto_selected(self):
        u = _controller_user("solo", allowed="GLS")
        self.client.force_login(u)
        resp = self.client.get(reverse("ui:hu_zone_select"))
        self.assertRedirects(resp, reverse("ui:hu_control_menu"))
        u.profile.refresh_from_db()
        self.assertEqual(u.profile.section, "GLS")

    def test_no_assigned_zones_keeps_legacy_behaviour(self):
        u = _controller_user("legacy")
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("ui:hu_control_menu")).status_code, 200)

    def test_post_switches_zone_and_rejects_forbidden(self):
        u = _controller_user("multi2", allowed="BUS,EXPORT", section="BUS")
        self.client.force_login(u)
        self.client.post(reverse("ui:hu_zone_select"), {"section": "EXPORT"})
        u.profile.refresh_from_db()
        self.assertEqual(u.profile.section, "EXPORT")
        self.client.post(reverse("ui:hu_zone_select"), {"section": "GLS"})
        u.profile.refresh_from_db()
        self.assertEqual(u.profile.section, "EXPORT")   # zabroniona — bez zmiany

    def test_zone_select_screen_lists_allowed_tiles(self):
        u = _controller_user("multi3", allowed="BUS,GLS", section="BUS")
        self.client.force_login(u)
        body = self.client.get(reverse("ui:hu_zone_select")).content.decode()
        self.assertIn("BUS + odbiory własne", body)
        self.assertIn("Paczka GLS", body)
        self.assertNotIn("Eksport", body)


class ZoneFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sh = Shipment.objects.create(name="D1")
        cls.bus = HandlingUnit.objects.create(shipment=sh, seq=1, code="HBUS",
                                              warehouse_type="92T3")
        cls.exp = HandlingUnit.objects.create(shipment=sh, seq=2, code="HEXP",
                                              warehouse_type="92EX")
        cls.other = HandlingUnit.objects.create(shipment=sh, seq=3, code="HOTH",
                                                warehouse_type="ZZ99")   # nieznany → GEIS

    def test_status_screen_shows_only_active_zone(self):
        u = _controller_user("busman", allowed="BUS,EXPORT", section="BUS")
        self.client.force_login(u)
        body = self.client.get(reverse("ui:hu_control_status")).content.decode()
        self.assertIn("HBUS", body)
        self.assertNotIn("HEXP", body)
        self.assertNotIn("HOTH", body)

    def test_geis_zone_catches_unmapped_types(self):
        u = _controller_user("geisman", allowed="GEIS,BUS", section="GEIS")
        self.client.force_login(u)
        body = self.client.get(reverse("ui:hu_control_status")).content.decode()
        self.assertIn("HOTH", body)
        self.assertNotIn("HBUS", body)
        self.assertNotIn("HEXP", body)

    def test_zone_ok_blocks_foreign_zone_writes(self):
        from huctl.views.hu_helpers import _zone_ok
        u = _controller_user("busman2", allowed="BUS", section="BUS")
        self.assertTrue(_zone_ok(u, self.bus))
        self.assertFalse(_zone_ok(u, self.exp))

    def test_history_of_foreign_zone_is_refused(self):
        u = _controller_user("busman3", allowed="BUS", section="BUS")
        self.client.force_login(u)
        resp = self.client.get(reverse("ui:hu_control_history", args=[self.exp.pk]))
        self.assertEqual(resp.status_code, 302)


class HeaderTests(TestCase):
    def test_header_has_zone_badge_and_no_bell(self):
        u = _controller_user("multi4", allowed="BUS,EXPORT", section="BUS")
        self.client.force_login(u)
        body = self.client.get(reverse("ui:hu_control_menu")).content.decode()
        self.assertIn("STREFA BUS", body)
        self.assertIn(reverse("ui:hu_zone_select"), body)   # badge klikalny (>1 strefa)
        self.assertNotIn("hu_notifications", body)          # dzwonek usunięty
        self.assertIn('id="msg-bell"', body)                # koperta zostaje

    def test_single_zone_badge_is_not_a_link(self):
        u = _controller_user("solo2", allowed="GLS", section="GLS")
        self.client.force_login(u)
        body = self.client.get(reverse("ui:hu_control_menu")).content.decode()
        self.assertIn("STREFA GLS", body)
        self.assertNotIn(reverse("ui:hu_zone_select"), body)


class NotifyMirrorTests(TestCase):
    def test_notification_lands_in_messenger_thread(self):
        from ui.notifications import notify, SYSTEM_THREAD_SUBJECT
        u = _controller_user("odbiorca")
        notify([u], "HU X wraca do rekontroli", body="Test", url="/control/")
        thread = MessageThread.objects.get(subject=SYSTEM_THREAD_SUBJECT, participants=u)
        self.assertEqual(thread.messages.count(), 1)
        self.assertIn("rekontroli", thread.messages.first().body)
        notify([u], "Drugie", body="")
        self.assertEqual(thread.messages.count(), 2)   # ten sam wątek, bez duplikatów
