"""Etykieta logistyczna (mini-wywiad 2026-07-30): ZPL z zawartością palety,
gated flagą klienta, język nagłówków per klient."""
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from ui.models import Customer, HandlingUnit, HandlingUnitItem, Shipment
from ui.roles import GROUP_CONTROLLER


def _controller(name="ktrl"):
    u = User.objects.create_user(name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class LogisticsLabelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _controller()
        cls.cust = Customer.objects.create(
            name="Demovo GmbH", requires_logistics_label=True, label_language="de",
            label_extra_text="Vertrag 77/2026", requirements_notes="Awizacja 24h")
        cls.sh = Shipment.objects.create(name="D-E1", customer=cls.cust,
                                         recipient_name="Demovo GmbH",
                                         destination_city="Berlin",
                                         destination_postal="10115",
                                         destination_country="DE")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HUE1",
                                             status="ok", verified_at=timezone.now())
        HandlingUnitItem.objects.create(hu=cls.hu, ref_code="ZR-100", description="Kompresy",
                                        base_qty=1200, base_unit="OP", alt_qty=24,
                                        alt_unit="KAR", lot="L123")

    def test_label_shows_pallet_x_of_y(self):
        # Eksport: „PALETA X z Y" — druga paleta w dostawie → 'PALETTE 1 / 2' (DE).
        HandlingUnit.objects.create(shipment=self.sh, seq=2, code="HUE2", status="planned")
        from ui.labels import zpl_logistics_label
        zpl = zpl_logistics_label(self.hu, self.cust)
        self.assertIn("PALETTE 1 / 2", zpl)
        self.assertIn("Demovo GmbH", zpl)             # nazwa odbiorcy
        self.assertIn("DE", zpl)                      # państwo

    def test_label_zpl_contents(self):
        self.client.force_login(self.user)
        r = self.client.get(f"/control/hu/{self.hu.pk}/label/")
        self.assertEqual(r.status_code, 200)
        zpl = r.content.decode()
        self.assertIn("LOGISTIKETIKETT", zpl)         # język DE z master daty klienta
        self.assertIn("ZR-100", zpl)
        self.assertIn("1200 OP / 24 KAR", zpl)
        self.assertIn("Demovo GmbH", zpl)
        self.assertIn("10115 Berlin DE", zpl)
        self.assertIn("Vertrag 77/2026", zpl)
        self.assertNotIn("^BC", zpl)                  # bez kodów kreskowych (decyzja)
        self.assertNotIn("^BQ", zpl)

    def test_label_blocked_without_customer_flag(self):
        self.cust.requires_logistics_label = False
        self.cust.save(update_fields=["requires_logistics_label"])
        self.client.force_login(self.user)
        r = self.client.get(f"/control/hu/{self.hu.pk}/label/", follow=True)
        self.assertNotIn("^XA", r.content.decode())   # redirect z komunikatem, nie ZPL
