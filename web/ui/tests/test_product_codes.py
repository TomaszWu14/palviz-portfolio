"""Rozwiązywanie kodów produktu: normalizacja wariantów mechanicznych + tabela
aliasów dla realnych zmian nazw."""
from django.test import TestCase

from ui.models import Product, ProductAlias
from ui.product_codes import normalize_code, resolve_product_code


class NormalizeCodeTests(TestCase):
    def test_case_and_separators(self):
        self.assertEqual(normalize_code("dmo-m"), "DMOM")
        self.assertEqual(normalize_code("dmo_m/100"), "DMOM100")

    def test_vendor_prefix_stripped(self):
        self.assertEqual(normalize_code("nieat-dmo"), "DMO")
        self.assertEqual(normalize_code("ACME_DMO"), "DMO")

    def test_version_and_batch_suffix_stripped(self):
        self.assertEqual(normalize_code("mdom10001_v1"), "MDOM10001")
        self.assertEqual(normalize_code("mdom10001_b1"), "MDOM10001")
        self.assertEqual(normalize_code("MDOM10001-V12"), "MDOM10001")

    def test_clean_code_unchanged(self):
        self.assertEqual(normalize_code("MDOM10001"), "MDOM10001")

    def test_empty(self):
        self.assertEqual(normalize_code(""), "")
        self.assertEqual(normalize_code(None), "")


class ResolveProductCodeTests(TestCase):
    def setUp(self):
        self.p = Product.objects.create(code="MDOM10001", name="Kompresy")

    def test_exact_code(self):
        self.assertEqual(resolve_product_code("MDOM10001"), self.p)

    def test_variant_suffix_resolves(self):
        self.assertEqual(resolve_product_code("mdom10001_v1"), self.p)
        self.assertEqual(resolve_product_code("mdom10001_b1"), self.p)

    def test_vendor_prefix_resolves(self):
        dmo = Product.objects.create(code="DMO", name="Rękawice")
        self.assertEqual(resolve_product_code("nieat-dmo"), dmo)

    def test_alias_table_for_real_rename(self):
        # DMO-M-100 → DMOM10001: różne cyfry, normalizacja nie wystarcza → alias.
        self.assertIsNone(resolve_product_code("DMO-M-100"))     # zanim dodamy alias
        ProductAlias.objects.create(product=self.p, alias_code="DMO-M-100")
        self.assertEqual(resolve_product_code("DMO-M-100"), self.p)

    def test_unknown_returns_none(self):
        self.assertIsNone(resolve_product_code("NIE-MA-TAKIEGO"))
        self.assertIsNone(resolve_product_code(""))
