"""P2: dokument WZ (kopie wg klienta), CSV dla przewoźnika, grupowanie tydzień/miesiąc
i grupa×sort na liście HU."""
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Shipment, HandlingUnit, HandlingUnitItem, Customer
from ui.roles import GROUP_TRANSPORT


def _tr(name="tr"):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_TRANSPORT)[0])
    return u


class WZDocument(TestCase):
    def setUp(self):
        self.cust = Customer.objects.create(name="Klinika Przykładowa", country="UA", wz_copies=3)
        self.sh = Shipment.objects.create(name="D1", customer=self.cust,
                                          wz_number="WZ/2026/77")
        hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="H1")
        HandlingUnitItem.objects.create(hu=hu, ref_code="A1", lot="L1",
                                        base_qty=10, base_unit="OP")
        hu2 = HandlingUnit.objects.create(shipment=self.sh, seq=2, code="H2")
        HandlingUnitItem.objects.create(hu=hu2, ref_code="A1", lot="L1",
                                        base_qty=5, base_unit="OP")
        self.client.force_login(_tr())

    def test_wz_renders_with_copies_and_aggregation(self):
        r = self.client.get(reverse("ui:planner_shipment_wz", args=[self.sh.pk]))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode() if r["Content-Type"].startswith("text") else ""
        if body:                                   # fallback HTML (brak WeasyPrint)
            self.assertIn("WZ/2026/77", body)
            self.assertIn("Klinika Przykładowa", body)
            self.assertEqual(body.count("Kopia "), 3)     # wz_copies klienta
            self.assertIn("15", body)                     # 10+5 zagregowane w jedną linię
        else:
            self.assertEqual(r["Content-Type"], "application/pdf")

    def test_carrier_csv_lists_hus(self):
        r = self.client.get(reverse("ui:planner_shipment_carrier_csv", args=[self.sh.pk]))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode("utf-8")
        self.assertIn("WZ/2026/77", body)
        self.assertIn("H1", body)
        self.assertIn("H2", body)


class WeekMonthGrouping(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="adm", password="x",
                                                 is_superuser=True)
        self.client.force_login(u)
        for i, d in enumerate([date(2026, 7, 1), date(2026, 7, 20), date(2026, 8, 3)]):
            sh = Shipment.objects.create(name=f"D{i}", outbound_created_date=d)
            HandlingUnit.objects.create(shipment=sh, seq=1, code=f"HU{i}")

    def test_month_buckets(self):
        r = self.client.get(reverse("ui:planner_stock_contents"),
                            {"view": "hu", "group": "month"})
        rows = r.context["group_rows"]
        self.assertEqual(len(rows), 2)                    # lipiec (2) + sierpień (1)
        self.assertEqual(sorted(x["n"] for x in rows), [1, 2])

    def test_group_sorted_by_value_when_sort_created(self):
        r = self.client.get(reverse("ui:planner_stock_contents"),
                            {"view": "hu", "group": "month", "sort": "created"})
        rows = r.context["group_rows"]
        self.assertEqual([x["n"] for x in rows], [2, 1])   # chronologicznie: VII przed VIII
