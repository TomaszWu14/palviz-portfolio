"""Lazy autocomplete REF (nadmiarowy towar): katalog indeksów nie jest już renderowany
inline w detalu HU — podpowiedzi zwraca endpoint hu_prod_codes (min 2 znaki, top-20)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Product
from ui.roles import GROUP_CONTROLLER


class HuProdCodesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = get_user_model().objects.create_user("c1", password="x")
        cls.u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        Product.objects.create(code="NL100-100", name="NONVI lux", is_active=True)
        Product.objects.create(code="XX200-1", name="Inny", is_active=True)

    def setUp(self):
        self.client.force_login(self.u)

    def test_query_filters_and_returns_matches(self):
        r = self.client.get(reverse("ui:hu_prod_codes"), {"q": "NL100"})
        codes = [p["code"] for p in r.json()["products"]]
        self.assertIn("NL100-100", codes)
        self.assertNotIn("XX200-1", codes)

    def test_short_query_returns_empty(self):
        r = self.client.get(reverse("ui:hu_prod_codes"), {"q": "N"})
        self.assertEqual(r.json()["products"], [])
