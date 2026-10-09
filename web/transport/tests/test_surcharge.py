"""Wycena — dopłaty (paliwowa % + stała) na ofertach spedycji."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import Shipment, ShipmentQuoteOffer
from ui.roles import GROUP_TRANSPORT, GROUP_MASTER_DATA


def _user(*groups):
    User = get_user_model()
    u = User.objects.create_user(f"u{User.objects.count()}", password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class TotalAmountTests(TestCase):
    def test_total_with_fuel_and_fixed(self):
        sh = Shipment.objects.create(name="S")
        o = ShipmentQuoteOffer.objects.create(shipment=sh, amount=Decimal("1000"),
                                              fuel_pct=Decimal("10"), surcharge_fixed=Decimal("50"))
        # 1000 + 10% (100) + 50 = 1150
        self.assertEqual(o.total_amount, Decimal("1150.00"))
        self.assertTrue(o.has_surcharge)

    def test_total_without_surcharge_equals_amount(self):
        sh = Shipment.objects.create(name="S")
        o = ShipmentQuoteOffer.objects.create(shipment=sh, amount=Decimal("800"))
        self.assertEqual(o.total_amount, Decimal("800"))
        self.assertFalse(o.has_surcharge)

    def test_total_none_when_no_amount(self):
        sh = Shipment.objects.create(name="S")
        o = ShipmentQuoteOffer.objects.create(shipment=sh, amount=None, fuel_pct=Decimal("10"))
        self.assertIsNone(o.total_amount)


class SurchargeEndpointTests(TestCase):
    def setUp(self):
        self.sh = Shipment.objects.create(name="S")
        self.offer = ShipmentQuoteOffer.objects.create(shipment=self.sh, carrier_name="Sped",
                                                        amount=Decimal("1000"))
        self.client.force_login(_user(GROUP_TRANSPORT))

    def test_set_surcharge(self):
        self.client.post(reverse("ui:planner_offer_surcharge", args=[self.offer.pk]),
                         {"fuel_pct": "12,5", "surcharge_fixed": "30", "surcharge_note": "ADR"})
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.fuel_pct, Decimal("12.5"))
        self.assertEqual(self.offer.surcharge_fixed, Decimal("30"))
        self.assertEqual(self.offer.surcharge_note, "ADR")

    def test_negative_clamped_to_zero(self):
        self.client.post(reverse("ui:planner_offer_surcharge", args=[self.offer.pk]),
                         {"fuel_pct": "-5", "surcharge_fixed": "abc"})
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.fuel_pct, Decimal("0"))
        self.assertEqual(self.offer.surcharge_fixed, Decimal("0"))

    def test_master_data_denied(self):
        self.client.force_login(_user(GROUP_MASTER_DATA))
        r = self.client.post(reverse("ui:planner_offer_surcharge", args=[self.offer.pk]),
                             {"fuel_pct": "5"})
        self.assertEqual(r.status_code, 403)


class QuoteHistoryUsesTotalTests(TestCase):
    def test_comparison_uses_total_amount(self):
        now = timezone.now()
        sh = Shipment.objects.create(name="Dostawa", destination_city="Berlin")
        # Cheaper base but big surcharge → ends up more expensive all-in.
        ShipmentQuoteOffer.objects.create(shipment=sh, carrier_name="A", amount=Decimal("900"),
                                          fuel_pct=Decimal("30"), submitted_at=now)   # total 1170
        ShipmentQuoteOffer.objects.create(shipment=sh, carrier_name="B", amount=Decimal("1000"),
                                          submitted_at=now)                            # total 1000
        self.client.force_login(_user(GROUP_TRANSPORT))
        ctx = self.client.get(reverse("ui:planner_quote_history")).context
        row = ctx["rows"][0]
        self.assertEqual(row["chosen"].carrier_name, "B")    # cheapest by total, not base
        self.assertEqual(row["best"], 1000.0)
        self.assertEqual(row["worst"], 1170.0)
        self.assertTrue(row["has_surcharge"])
