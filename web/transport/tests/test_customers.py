"""Customer database + per-customer delivery requirements.

Covers CRUD access, and that a linked customer's max pallet height is enforced as
a hard cap on the calculator scenarios (never propose a taller pallet)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import Product, Shipment, ShipmentLine, Customer
from ui.roles import ALL_GROUPS


def _user():
    u = get_user_model().objects.create_user(username="cust", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class CustomerCrudTests(TestCase):
    def setUp(self):
        self.client.force_login(_user())

    def test_create_and_list_customer(self):
        resp = self.client.post(reverse("ui:planner_customer_new"), {
            "name": "PHARMO", "code": "F01", "kind": "customer",
            "country": "PL", "city": "Łódź", "postal": "90-001",
            "max_pallet_height_cm": "190", "requires_fumigated_pallet": "on",
            "pallet_type": "euro", "is_active": "on",
        })
        self.assertRedirects(resp, reverse("ui:planner_customers"))
        c = Customer.objects.get(code="F01")
        self.assertEqual(c.max_pallet_height_cm, 190)
        self.assertTrue(c.requires_fumigated_pallet)
        self.assertTrue(c.has_requirements())
        # Appears on the list page.
        lst = self.client.get(reverse("ui:planner_customers"))
        self.assertContains(lst, "PHARMO")


class CustomerHeightCapTests(TestCase):
    def setUp(self):
        self.client.force_login(_user())
        p = Product.objects.create(code="P1", name="Produkt")
        self.customer = Customer.objects.create(name="LowCeiling", max_pallet_height_cm=190)
        self.sh = Shipment.objects.create(name="S1", customer=self.customer)
        ShipmentLine.objects.create(shipment=self.sh, product=p, quantity=20, unit="kar")

    def test_height_scenarios_capped_to_customer_max(self):
        # Ask for a tall 2.5 m scenario — the 190 cm customer cap must win.
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]),
                               {"h1": "1.8", "h2": "2.5"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["height_capped"])
        self.assertEqual(resp.context["customer"], self.customer)
        heights = [s["max_h_cm"] for s in resp.context["calc"]["scenarios"]]
        self.assertTrue(heights)
        self.assertLessEqual(max(heights), 190)

    def test_requirements_banner_renders(self):
        self.customer.requires_fumigated_pallet = True
        self.customer.save()
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]))
        self.assertContains(resp, "Wymagania klienta")
        self.assertContains(resp, "fumigowana")


# SAP KNA1-style export (Polish headers, ';'-separated), mirroring the real file.
KNA1 = (
    "Klient;Klucz kraju/regionu;Nazwa 1;Nazwa 2;Miasto;Kod pocztowy;Region;Szukany ciąg zn.;Ulica;Telefon 1\n"
    "10000000;PL;ACME sp. z o.o.;;Radom;26-603;SLS;ACME;Ul. Magazynowa 44;\n"
    "10000003;PL;DEMOLAB SPÓŁKA Z OGRANICZONĄ;ODPOWIEDZIALNOŚCIĄ;Warszawa;00-002;MAZ;DEMOLAB;ul. Testowa 2;600000000\n"
)


class CustomerImportTests(TestCase):
    def setUp(self):
        self.client.force_login(_user())

    def _upload(self, text=KNA1):
        f = SimpleUploadedFile("kna1.csv", text.encode("utf-8"), content_type="text/csv")
        return self.client.post(reverse("ui:planner_customer_import"), {"file": f})

    def test_import_creates_customers_with_combined_name(self):
        resp = self._upload()
        self.assertRedirects(resp, reverse("ui:planner_customers"))
        self.assertEqual(Customer.objects.count(), 2)
        z = Customer.objects.get(code="10000000")
        self.assertEqual(z.name, "ACME sp. z o.o.")
        self.assertEqual(z.city, "Radom")
        self.assertEqual(z.country, "PL")
        # Two name columns are joined.
        demolab = Customer.objects.get(code="10000003")
        self.assertEqual(demolab.name, "DEMOLAB SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ")
        self.assertEqual(demolab.street, "ul. Testowa 2")
        self.assertEqual(demolab.phone, "600000000")

    def test_reimport_refreshes_address_but_preserves_requirements(self):
        # A customer already curated in PalViz with a delivery requirement.
        Customer.objects.create(code="10000000", name="OLD NAME", city="Radom",
                                max_pallet_height_cm=190, requires_fumigated_pallet=True)
        self._upload()
        z = Customer.objects.get(code="10000000")
        self.assertEqual(Customer.objects.filter(code="10000000").count(), 1)  # upsert, not dup
        self.assertEqual(z.name, "ACME sp. z o.o.")   # refreshed
        self.assertEqual(z.city, "Radom")                          # refreshed
        self.assertEqual(z.max_pallet_height_cm, 190)               # preserved
        self.assertTrue(z.requires_fumigated_pallet)                # preserved


# The preferred master-data layout: a single combined "Nazwa klienta" column.
KNA1_SINGLE = (
    "Klient;Klucz kraju/regionu;Nazwa klienta;Miasto;Kod pocztowy;Region;Szukany ciąg zn.;Ulica;Telefon 1\n"
    "10000000;PL;ACME sp. z o.o.;Radom;26-603;SLS;ACME;Ul. Magazynowa 44;\n"
    "10000004;PL;ZAKŁAD WYROBÓW PRZYKŁADOWYCH \"DEMOMED\";Radom;26-600;SLS;DEMOMED;ul. Testowa 1;480000000\n"
)


class CustomerImportSingleNameTests(TestCase):
    def setUp(self):
        self.client.force_login(_user())

    def test_single_nazwa_klienta_column_imports(self):
        f = SimpleUploadedFile("kna1.csv", KNA1_SINGLE.encode("utf-8"), content_type="text/csv")
        resp = self.client.post(reverse("ui:planner_customer_import"), {"file": f})
        self.assertRedirects(resp, reverse("ui:planner_customers"))
        self.assertEqual(Customer.objects.count(), 2)
        c = Customer.objects.get(code="10000004")
        self.assertEqual(c.name, 'ZAKŁAD WYROBÓW PRZYKŁADOWYCH "DEMOMED"')
        self.assertEqual(c.city, "Radom")
        self.assertEqual(c.street, "ul. Testowa 1")
        self.assertEqual(c.phone, "480000000")
