"""Wycena — przekrojowa historia wycen (oferty spedycji per przesyłka)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import Shipment, ShipmentQuoteOffer
from ui.roles import GROUP_TRANSPORT, GROUP_MASTER_DATA


def _user(*groups):
    User = get_user_model()
    name = f"u{User.objects.count()}"
    u = User.objects.create_user(name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class QuoteHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        # Shipment with two submitted offers — selected one is the pricier (manual choice).
        cls.sh1 = Shipment.objects.create(name="Dostawa A", destination_city="Berlin")
        ShipmentQuoteOffer.objects.create(shipment=cls.sh1, carrier_name="Tani Sped",
                                          amount=800, currency="PLN", submitted_at=now)
        ShipmentQuoteOffer.objects.create(shipment=cls.sh1, carrier_name="Wybrana Sped",
                                          amount=1000, currency="PLN", submitted_at=now, selected=True)
        # Shipment with one submitted + one unsubmitted (draft) offer.
        cls.sh2 = Shipment.objects.create(name="Dostawa B", destination_city="Praga")
        ShipmentQuoteOffer.objects.create(shipment=cls.sh2, carrier_name="Jedyna",
                                          amount=500, currency="PLN", submitted_at=now)
        ShipmentQuoteOffer.objects.create(shipment=cls.sh2, carrier_name="Niezłożona",
                                          amount=400, currency="PLN", submitted_at=None)
        # Shipment without any submitted offer — must not appear.
        cls.sh3 = Shipment.objects.create(name="Bez ofert", destination_city="Wiedeń")
        ShipmentQuoteOffer.objects.create(shipment=cls.sh3, carrier_name="X",
                                          amount=999, submitted_at=None)
        # Stock container — excluded from transport history.
        cls.stock = Shipment.objects.create(name="Stock", is_stock=True)
        ShipmentQuoteOffer.objects.create(shipment=cls.stock, carrier_name="Y",
                                          amount=1, submitted_at=now)

    def setUp(self):
        self.client.force_login(_user(GROUP_TRANSPORT))

    def test_lists_only_shipments_with_submitted_offers(self):
        resp = self.client.get(reverse("ui:planner_quote_history"))
        self.assertEqual(resp.status_code, 200)
        names = [r["sh"].name for r in resp.context["rows"]]
        self.assertIn("Dostawa A", names)
        self.assertIn("Dostawa B", names)
        self.assertNotIn("Bez ofert", names)
        self.assertNotIn("Stock", names)

    def test_selected_offer_and_saving(self):
        resp = self.client.get(reverse("ui:planner_quote_history"))
        row_a = next(r for r in resp.context["rows"] if r["sh"].name == "Dostawa A")
        self.assertTrue(row_a["is_selected"])
        self.assertEqual(row_a["chosen"].carrier_name, "Wybrana Sped")
        self.assertEqual(row_a["best"], 1000.0)        # the manually selected offer
        self.assertEqual(row_a["worst"], 1000.0)       # max amount among offers
        self.assertEqual(row_a["saving"], 0.0)

    def test_cheapest_used_when_none_selected(self):
        resp = self.client.get(reverse("ui:planner_quote_history"))
        row_b = next(r for r in resp.context["rows"] if r["sh"].name == "Dostawa B")
        self.assertFalse(row_b["is_selected"])
        self.assertEqual(row_b["best"], 500.0)         # only the submitted offer counts
        self.assertEqual(row_b["n_offers"], 1)

    def test_search_filter(self):
        resp = self.client.get(reverse("ui:planner_quote_history"), {"q": "Berlin"})
        names = [r["sh"].name for r in resp.context["rows"]]
        self.assertEqual(names, ["Dostawa A"])

    def test_master_data_only_denied(self):
        self.client.force_login(_user(GROUP_MASTER_DATA))
        self.assertEqual(self.client.get(reverse("ui:planner_quote_history")).status_code, 403)

    def test_csv_export(self):
        resp = self.client.get(reverse("ui:planner_quote_history"), {"export": "csv"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", resp["Content-Disposition"])
        body = resp.content.decode("utf-8")
        self.assertIn("Przesyłka", body)            # header row
        self.assertIn("Dostawa A", body)
        self.assertIn("Dostawa B", body)
        self.assertNotIn("Bez ofert", body)         # only submitted-offer rows

    def test_csv_export_respects_search(self):
        resp = self.client.get(reverse("ui:planner_quote_history"),
                               {"export": "csv", "q": "Berlin"})
        body = resp.content.decode("utf-8")
        self.assertIn("Dostawa A", body)
        self.assertNotIn("Dostawa B", body)
