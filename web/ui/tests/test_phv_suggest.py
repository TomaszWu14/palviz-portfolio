"""Typeahead wyszukiwarki PHV: podpowiedzi indeksu od pierwszej litery."""
import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class PhvSuggestTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("phv_admin", "a@a.pl", "x")
        self.client.force_login(self.admin)
        from ui.models import Product
        Product.objects.create(code="DMOM10001", name="Rękawice nitrylowe M")
        Product.objects.create(code="DMOM10002", name="Rękawice nitrylowe L")
        Product.objects.create(code="ABC-500", name="Kompresy gazowe do ran")
        self.url = reverse("ui:phv_suggest")

    def _get(self, q):
        return json.loads(self.client.get(self.url, {"q": q}).content)["results"]

    def test_prefix_first_letter(self):
        codes = [r["code"] for r in self._get("dmom")]   # od pierwszej litery (prefiks kodu)
        self.assertEqual(set(codes), {"DMOM10001", "DMOM10002"})
        self.assertNotIn("ABC-500", codes)

    def test_single_letter_prefix_ranks_code_first(self):
        codes = [r["code"] for r in self._get("d")]      # już po 1. literze coś jest
        self.assertIn("DMOM10001", codes)
        # kody-prefiksy przed dobranymi po fragmencie nazwy
        self.assertLess(codes.index("DMOM10001"), codes.index("ABC-500"))

    def test_empty_query(self):
        self.assertEqual(self._get(""), [])

    def test_no_match(self):
        self.assertEqual(self._get("ZZZZ"), [])

    def test_matches_name_fragment(self):
        codes = [r["code"] for r in self._get("kompresy")]
        self.assertIn("ABC-500", codes)
