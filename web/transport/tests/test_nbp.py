"""Wycena — przeliczanie ofert walutowych na PLN po kursie NBP."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui import nbp
from ui.models import Shipment, ShipmentQuoteOffer
from ui.roles import GROUP_TRANSPORT


def _user(*groups):
    User = get_user_model()
    u = User.objects.create_user(f"u{User.objects.count()}", password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class NbpHelperTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_pln_is_identity(self):
        self.assertEqual(nbp.get_rate("PLN"), Decimal("1"))
        self.assertEqual(nbp.get_rate(""), Decimal("1"))

    def test_rate_fetched_and_cached(self):
        fake = {"rates": [{"mid": 4.3210}]}

        class _Resp:
            def raise_for_status(self): pass
            def json(self): return fake
        with patch("requests.get", return_value=_Resp()) as g:
            r1 = nbp.get_rate("EUR")
            r2 = nbp.get_rate("EUR")            # second call served from cache
        self.assertEqual(r1, Decimal("4.3210"))
        self.assertEqual(r2, Decimal("4.3210"))
        self.assertEqual(g.call_count, 1)       # cached → only one HTTP call

    def test_network_failure_returns_none(self):
        with patch("requests.get", side_effect=OSError("offline")):
            self.assertIsNone(nbp.get_rate("USD"))

    def test_to_pln(self):
        with patch("ui.nbp.get_rate", return_value=Decimal("4")):
            self.assertEqual(nbp.to_pln(Decimal("100"), "EUR"), Decimal("400"))
        with patch("ui.nbp.get_rate", return_value=None):
            self.assertIsNone(nbp.to_pln(Decimal("100"), "EUR"))


class QuoteHistoryNbpTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.force_login(_user(GROUP_TRANSPORT))

    def _rate(self, cur):
        return {"PLN": Decimal("1"), "EUR": Decimal("4.0"), "USD": Decimal("3.5")}.get(cur)

    def test_mixed_currencies_ranked_in_pln(self):
        now = timezone.now()
        sh = Shipment.objects.create(name="Mix", destination_city="Berlin")
        # 250 EUR = 1000 PLN  vs  900 PLN → PLN winner is the 900 PLN offer.
        ShipmentQuoteOffer.objects.create(shipment=sh, carrier_name="EUR", amount=Decimal("250"),
                                          currency="EUR", submitted_at=now)
        ShipmentQuoteOffer.objects.create(shipment=sh, carrier_name="PLN", amount=Decimal("900"),
                                          currency="PLN", submitted_at=now)
        with patch("ui.nbp.get_rate", side_effect=self._rate):
            row = self.client.get(reverse("ui:planner_quote_history")).context["rows"][0]
        self.assertTrue(row["normalized"])
        self.assertEqual(row["currency"], "PLN")
        self.assertEqual(row["chosen"].carrier_name, "PLN")   # 900 PLN < 1000 PLN
        self.assertEqual(row["best"], 900.0)
        self.assertEqual(row["worst"], 1000.0)

    def test_single_currency_not_normalized(self):
        now = timezone.now()
        sh = Shipment.objects.create(name="Eur only", destination_city="Praga")
        ShipmentQuoteOffer.objects.create(shipment=sh, carrier_name="A", amount=Decimal("100"),
                                          currency="EUR", submitted_at=now)
        with patch("ui.nbp.get_rate", side_effect=self._rate):
            row = self.client.get(reverse("ui:planner_quote_history")).context["rows"][0]
        self.assertFalse(row["normalized"])
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["chosen_pln"], 400.0)            # 100 EUR ≈ 400 PLN shown

    def test_missing_rate_falls_back_to_raw(self):
        now = timezone.now()
        sh = Shipment.objects.create(name="No rate", destination_city="Rzym")
        ShipmentQuoteOffer.objects.create(shipment=sh, carrier_name="E", amount=Decimal("100"),
                                          currency="EUR", submitted_at=now)
        ShipmentQuoteOffer.objects.create(shipment=sh, carrier_name="P", amount=Decimal("50"),
                                          currency="PLN", submitted_at=now)
        with patch("ui.nbp.get_rate", return_value=None):     # NBP unreachable
            row = self.client.get(reverse("ui:planner_quote_history")).context["rows"][0]
        self.assertFalse(row["normalized"])                   # no normalization without rates
