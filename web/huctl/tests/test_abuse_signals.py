"""Warstwa C — karta „Sygnały nadużyć" na panelu lidera: pozycje liczone <3 s
i wpisy ręczne (input_source=keyboard) per kontroler, z dzisiejszego audytu."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import HandlingUnit, HandlingUnitItem, HUControlAttempt, Shipment
from ui.roles import GROUP_CONTROLLER


class AbuseSignalsTests(TestCase):
    def setUp(self):
        self.lead = get_user_model().objects.create_superuser("lead", "l@l.pl", "x")
        self.c1 = get_user_model().objects.create_user("c1", password="x")
        self.c1.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="H1", warehouse_type="92EX")
        self.it = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A1", base_qty=5)
        self.client.force_login(self.lead)

    def _att(self, **kw):
        HUControlAttempt.objects.create(hu=self.hu, item=self.it, controller=self.c1,
                                        exp_base_qty=5, **kw)

    def test_fast_and_keyboard_counted(self):
        self._att(seconds_since_prev=1.2, input_source="scan")     # <3 s → fast
        self._att(seconds_since_prev=45,  input_source="keyboard") # ręczny wpis
        self._att(seconds_since_prev=30,  input_source="scan")     # czysty
        r = self.client.get(reverse("ui:hu_control_leader"))
        rows = r.context["abuse_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["controller"], "c1")
        self.assertEqual(rows[0]["fast"], 1)
        self.assertEqual(rows[0]["keyboard"], 1)
        self.assertContains(r, "Sygnały nadużyć")

    def test_clean_controller_not_listed(self):
        self._att(seconds_since_prev=30, input_source="scan")
        r = self.client.get(reverse("ui:hu_control_leader"))
        self.assertEqual(r.context["abuse_rows"], [])
        self.assertNotContains(r, "Sygnały nadużyć")
