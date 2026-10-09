"""Ekran detalu HU nie może skalować zapytań liczbą pozycji (N+1).

`_alt_conv` i `_unit_factors` pytały o instrukcję paletyzacji niezależnie od siebie,
więc paleta z N pozycjami generowała 2N zapytań o master data — przy 30 pozycjach
to 60 round-tripów na jedno otwarcie palety w hali."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from ui.models import (HandlingUnit, HandlingUnitItem, PalletizationInstruction,
                       Product, Shipment)
from ui.roles import GROUP_CONTROLLER


def _instruction(product, ppc):
    return PalletizationInstruction.objects.create(
        product=product, version=1, is_active=True, pcs_per_carton=ppc,
        carton_l=40, carton_w=30, carton_h=20, unit_weight=0.5)


class DetailQueryCountTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="perf", password="x")
        self.user.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        self.client.force_login(self.user)
        self.sh = Shipment.objects.create(name="D1")

    def _hu_with(self, n_products):
        hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code=f"PERF{n_products}",
                                         status="in_control")
        for i in range(n_products):
            p = Product.objects.create(code=f"P{i}", name=f"Produkt {i}")
            _instruction(p, ppc=10 + i)
            HandlingUnitItem.objects.create(hu=hu, product=p, ref_code=p.code,
                                            base_unit="OP", base_qty=100)
        return hu

    def _instr_queries(self, hu):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse("ui:hu_control_detail", args=[hu.pk]))
        self.assertEqual(resp.status_code, 200)
        return [q for q in ctx.captured_queries
                if "ui_palletizationinstruction" in q["sql"].lower()]

    def test_instruction_queries_do_not_grow_with_positions(self):
        few = len(self._instr_queries(self._hu_with(2)))
        HandlingUnit.objects.all().delete()
        Product.objects.all().delete()
        many = len(self._instr_queries(self._hu_with(12)))
        self.assertEqual(few, many,
                         f"N+1: {few} zapytań przy 2 pozycjach, {many} przy 12")

    def test_conversion_still_correct_after_prefetch(self):
        # Cache nie może pomieszać instrukcji między produktami — każda pozycja
        # ma inny przelicznik, a ekran pokazuje je w kafelkach KAR.
        hu = self._hu_with(3)
        body = self.client.get(reverse("ui:hu_control_detail", args=[hu.pk])).content.decode()
        for ppc in (10, 11, 12):
            self.assertIn(f'data-factor="{ppc}"', body)

    def test_newest_active_version_wins(self):
        hu = HandlingUnit.objects.create(shipment=self.sh, seq=9, code="VER1",
                                         status="in_control")
        p = Product.objects.create(code="PV", name="Wersjonowany")
        _instruction(p, ppc=10)
        PalletizationInstruction.objects.create(
            product=p, version=2, is_active=True, pcs_per_carton=24,
            carton_l=40, carton_w=30, carton_h=20, unit_weight=0.5)
        HandlingUnitItem.objects.create(hu=hu, product=p, ref_code=p.code,
                                        base_unit="OP", base_qty=100)

        body = self.client.get(reverse("ui:hu_control_detail", args=[hu.pk])).content.decode()
        self.assertIn('data-factor="24"', body)
        self.assertNotIn('data-factor="10"', body)
