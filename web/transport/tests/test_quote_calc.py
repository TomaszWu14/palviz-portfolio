"""Carrier-quote matching: zones, wildcard, unmatched-country skip, currency compare."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Carrier, CarrierZone, CarrierRate, Shipment, ShipmentQuoteOffer
from ui.roles import ALL_GROUPS
from ui.views.core.helpers import _calc_carrier_quotes


def _user():
    u = get_user_model().objects.create_user(username="q", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


CALC = {"total_vol_m3": 1.0, "total_weight_kg": 100.0, "scenarios": [{"n_pallets": 1}]}


class ZoneMatchTests(TestCase):
    def _carrier(self, countries):
        c = Carrier.objects.create(name=f"C-{countries or 'any'}", carrier_type="ltl")
        z = CarrierZone.objects.create(carrier=c, name="z", countries=countries)
        CarrierRate.objects.create(zone=z, weight_from_kg=0, price_eur=100)
        return c

    def _quotes(self, country):
        carriers = Carrier.objects.filter(is_active=True).prefetch_related("zones__rates")
        return _calc_carrier_quotes(CALC, carriers, country)

    def test_specific_country_matches(self):
        self._carrier("DE,AT")
        self.assertEqual(len(self._quotes("DE")), 1)

    def test_unmatched_country_is_skipped_not_mispriced(self):
        self._carrier("DE")                       # only serves DE
        self.assertEqual(len(self._quotes("FR")), 0)   # no fabricated price for FR

    def test_empty_countries_zone_is_wildcard(self):
        self._carrier("")                          # serves anywhere
        self.assertEqual(len(self._quotes("FR")), 1)

    def test_no_destination_uses_first_zone(self):
        self._carrier("DE")
        self.assertEqual(len(self._quotes("")), 1)


class SelectOfferTests(TestCase):
    def setUp(self):
        self.client.force_login(_user())
        self.sh = Shipment.objects.create(name="S1")

    def test_select_offer_without_recipient_does_not_crash(self):
        # recipient deleted (SET_NULL) + blank carrier_name → must not AttributeError.
        from django.utils import timezone
        offer = ShipmentQuoteOffer.objects.create(shipment=self.sh, recipient=None,
                                                  carrier_name="", amount=100,
                                                  submitted_at=timezone.now())   # BIZ-009
        resp = self.client.post(reverse("ui:planner_shipment_select_offer", args=[self.sh.pk, offer.pk]))
        self.assertEqual(resp.status_code, 302)
        offer.refresh_from_db()
        self.assertTrue(offer.selected)

    def test_mixed_currency_not_flagged_single(self):
        from django.utils import timezone
        for amt, cur in [(100, "EUR"), (500, "PLN")]:
            ShipmentQuoteOffer.objects.create(shipment=self.sh, carrier_name=cur,
                                              amount=amt, currency=cur, submitted_at=timezone.now())
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]))
        self.assertFalse(resp.context["quote_single_currency"])
