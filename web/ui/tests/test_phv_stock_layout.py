"""PHV „DANE MAGAZYNOWE”: „strefa wydawcza” wpada w linię chipów (nie w nagłówek panelu)
i wiersze typów mają wyraźniejszy separator, żeby typy dało się odróżnić."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import (Product, PalletizationInstruction, Shipment, HandlingUnit,
                       HandlingUnitItem)
from ui.roles import GROUP_WAREHOUSE


def _wh():
    u = get_user_model().objects.create_user(username="mag", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
    return u


def _product(code="R90"):
    p = Product.objects.create(code=code, name="Rękawice", unit_length_cm=21,
                               unit_width_cm=12, unit_height_cm=5.5)
    PalletizationInstruction.objects.create(
        product=p, name="v1", is_active=True, version=1, unit_weight=0.4,
        pcs_per_carton=10, carton_l=29, carton_w=25, carton_h=22,
        pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15,
        max_height_total_cm=213)
    return p


class PhvStockLayoutTests(TestCase):
    def setUp(self):
        self.client.force_login(_wh())

    def _render_with_stock(self):
        p = _product()
        sh = Shipment.objects.create(name="Stock", is_stock=True)
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HU-0010",
                                         warehouse_type="0010", location="C1-02-030B")
        HandlingUnitItem.objects.create(hu=hu, product=p, ref_code=p.code)
        return self.client.get(reverse("ui:phv_home"), {"q": p.code}).content.decode()

    def test_strefa_label_in_chip_bar_and_stronger_separator(self):
        html = self._render_with_stock()
        panel = html.split("data-phv-panel", 1)[1]
        # Nagłówek panelu (summary) NIE zawiera już etykiety strefy.
        header = panel.split("</summary>", 1)[0]
        self.assertNotIn("wydawcz", header)
        # Etykieta strefy jest w pasku chipów (unikalny span z white-space:nowrap).
        bar = panel.split("phv-cat-bar", 1)
        self.assertEqual(len(bar), 2, "brak paska chipów")
        chip_bar = bar[1].split("</div>", 1)[0]
        self.assertIn("wydawcz", chip_bar)                  # etykieta w linii chipów
        self.assertIn("#c5ddd8", html)                      # wyraźniejszy separator typów
