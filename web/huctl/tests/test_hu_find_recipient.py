"""Znajdź dla odbiorcy: skan pickHU → inne HU tego samego odbiorcy, także w innych strefach."""
from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Shipment, HandlingUnit, ControllerZone


class FindRecipientScanTests(TestCase):
    def setUp(self):
        self.sh1 = Shipment.objects.create(recipient_name="PHARMO", kunnr="K1")
        self.sh2 = Shipment.objects.create(recipient_name="INNY", kunnr="K2")
        # Ten sam odbiorca (K1), DWIE różne strefy magazynu.
        self.hu1 = HandlingUnit.objects.create(shipment=self.sh1, seq=1, code="P1", warehouse_type="A")
        self.hu2 = HandlingUnit.objects.create(shipment=self.sh1, seq=2, code="P2", warehouse_type="B")
        # Inny odbiorca — nie powinien się pojawić.
        self.hu3 = HandlingUnit.objects.create(shipment=self.sh2, seq=1, code="P9", warehouse_type="A")
        self.url = reverse("ui:hu_control_find_recipient")

    def test_scan_returns_siblings_across_zones(self):
        self.client.force_login(User.objects.create_superuser("adm", "a@a.pl", "x"))
        r = self.client.get(self.url, {"hu": "P1"})
        self.assertEqual(r.status_code, 200)
        codes = {h.code for h in r.context["hus"]}
        self.assertEqual(codes, {"P2"})                      # rodzeństwo K1, bez P1 i bez K2
        self.assertEqual(r.context["recipient_label"], "PHARMO")

    def test_out_of_zone_flag_for_controller(self):
        u = User.objects.create_user("ctrl", "c@c.pl", "x")
        u.groups.add(Group.objects.get_or_create(name="Kontrola HU")[0])
        ControllerZone.objects.create(user=u, code="A")      # tylko strefa A
        self.client.force_login(u)
        r = self.client.get(self.url, {"hu": "P1"})
        hus = {h.code: h for h in r.context["hus"]}
        self.assertIn("P2", hus)
        self.assertTrue(hus["P2"].out_of_zone)               # P2 jest w strefie B → podgląd

    def test_unknown_hu(self):
        self.client.force_login(User.objects.create_superuser("adm2", "a2@a.pl", "x"))
        r = self.client.get(self.url, {"hu": "NIEMA"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(r.context["hus"]), [])
