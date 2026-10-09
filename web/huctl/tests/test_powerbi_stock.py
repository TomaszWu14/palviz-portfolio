"""Pulling warehouse stock from Power BI feeds the same HU importer; unconfigured
Power BI fails gracefully."""
from unittest import mock
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from ui import models as m


class PowerBiStockTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})

    def test_not_configured_shows_error(self):
        # No POWERBI_* settings → friendly error, no crash.
        r = self.client.post(reverse("ui:planner_stock_powerbi_import"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(m.HandlingUnit.objects.count(), 0)

    @override_settings(POWERBI_WORKSPACE_ID="w", POWERBI_DATASET_ID="d",
                       POWERBI_ACCESS_TOKEN="tok")
    def test_pull_imports_handling_units(self):
        # Power BI returns rows keyed 'Table'[Column]; fetch_table normalises to
        # (header, rows). Mock it so no network/credentials are needed.
        header = ["jednostka obsługi", "produkt", "ilość", "typ magazynu"]
        rows = [
            ["HU001", "DMOM10001", "120", "WCGL"],
            ["HU002", "DMOM10001", "60", "WCGL"],
        ]
        m.Product.objects.create(code="DMOM10001", name="Rękawice M")
        with mock.patch("ui.powerbi.fetch_table", return_value=(header, rows)):
            r = self.client.post(reverse("ui:planner_stock_powerbi_import"))
        self.assertEqual(r.status_code, 302)
        stock = m.Shipment.objects.get(name="Stock magazynowy")
        self.assertTrue(stock.is_stock)
        self.assertEqual(stock.handling_units.count(), 2)
        hu = stock.handling_units.get(code="HU001")
        self.assertEqual(hu.warehouse_type, "WCGL")
        self.assertEqual(hu.items.count(), 1)

    @override_settings(POWERBI_WORKSPACE_ID="w", POWERBI_DATASET_ID="d",
                       POWERBI_ACCESS_TOKEN="tok")
    def test_empty_result_warns(self):
        with mock.patch("ui.powerbi.fetch_table", return_value=([], [])):
            r = self.client.post(reverse("ui:planner_stock_powerbi_import"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(m.HandlingUnit.objects.count(), 0)

    @override_settings(POWERBI_WORKSPACE_ID="w", POWERBI_DATASET_ID="d",
                       POWERBI_ACCESS_TOKEN="tok")
    def test_fetch_error_hides_raw_exception(self):
        # INT-006: użytkownik widzi komunikat PL, szczegół idzie do logu i last_error.
        secret = "https://api.powerbi.com/v1.0/myorg/groups/WS-SECRET/datasets/DS-SECRET"
        with mock.patch("ui.powerbi.fetch_table", side_effect=RuntimeError(secret)), \
                self.assertLogs("ui.powerbi", "ERROR"):
            r = self.client.post(reverse("ui:planner_stock_powerbi_import"), follow=True)
        self.assertContains(r, "Nie udało się pobrać danych z Power BI")
        self.assertNotContains(r, "WS-SECRET")
        self.assertIn("WS-SECRET", m.PowerBIToken.load().last_error)


class PowerBiConfigTests(TestCase):
    def test_is_configured_token_or_service_principal(self):
        from ui import powerbi
        with override_settings(POWERBI_WORKSPACE_ID="", POWERBI_DATASET_ID=""):
            self.assertFalse(powerbi.is_configured())
        with override_settings(POWERBI_WORKSPACE_ID="w", POWERBI_DATASET_ID="d",
                               POWERBI_ACCESS_TOKEN="tok"):
            self.assertTrue(powerbi.is_configured())
        with override_settings(POWERBI_WORKSPACE_ID="w", POWERBI_DATASET_ID="d",
                               POWERBI_ACCESS_TOKEN="", POWERBI_TENANT_ID="t",
                               POWERBI_CLIENT_ID="c", POWERBI_CLIENT_SECRET="s"):
            self.assertTrue(powerbi.is_configured())
