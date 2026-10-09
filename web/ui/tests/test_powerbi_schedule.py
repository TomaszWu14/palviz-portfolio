"""Zaplanowane pobranie stocku z Power BI (beat task) + przeliczenie niezgodności."""
from unittest.mock import patch

from django.test import SimpleTestCase

from ui.tasks import scheduled_powerbi_stock_pull


class ScheduledPowerBIPullTests(SimpleTestCase):
    def test_noop_when_not_configured(self):
        with patch("ui.powerbi.is_configured", return_value=False):
            result = scheduled_powerbi_stock_pull()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "powerbi_not_configured")

    def test_noop_when_no_rows(self):
        with patch("ui.powerbi.is_configured", return_value=True), \
             patch("ui.powerbi.fetch_table", return_value=(["a"], [])):
            result = scheduled_powerbi_stock_pull()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_rows")

    def test_fetch_error_is_caught(self):
        with patch("ui.powerbi.is_configured", return_value=True), \
             patch("ui.powerbi.fetch_table", side_effect=RuntimeError("boom")):
            result = scheduled_powerbi_stock_pull()
        self.assertFalse(result["ok"])
        self.assertIn("fetch_error", result["reason"])

    def test_happy_path_imports_and_checks(self):
        with patch("ui.powerbi.is_configured", return_value=True), \
             patch("ui.powerbi.fetch_table", return_value=(["h"], [["r"]])), \
             patch("huctl.hu_import.import_hu_rows", return_value=(True, {"hu": 3, "items": 12})) as imp, \
             patch("ui.notifications.run_stock_discrepancy_checks", return_value=2) as chk:
            result = scheduled_powerbi_stock_pull()
        imp.assert_called_once()
        chk.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertEqual(result["hu"], 3)
        self.assertEqual(result["items"], 12)
        self.assertEqual(result["discrepancies"], 2)

    def test_import_failure_skips_checks(self):
        with patch("ui.powerbi.is_configured", return_value=True), \
             patch("ui.powerbi.fetch_table", return_value=(["h"], [["r"]])), \
             patch("huctl.hu_import.import_hu_rows", return_value=(False, "zły format")), \
             patch("ui.notifications.run_stock_discrepancy_checks") as chk:
            result = scheduled_powerbi_stock_pull()
        chk.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "zły format")
