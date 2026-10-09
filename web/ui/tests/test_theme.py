from types import SimpleNamespace

from django.test import SimpleTestCase

from ui.theme import theme_for, carrier_for, shipment_type_for, theme_for_carrier


class ThemeMappingTests(SimpleTestCase):
    def test_known_zone_codes_map_to_carrier(self):
        self.assertEqual(carrier_for("92T3"), "BUS")
        self.assertEqual(carrier_for("92JU"), "BUS")
        self.assertEqual(carrier_for("92GE"), "GEIS")
        self.assertEqual(carrier_for("92EX"), "EXPORT")
        self.assertEqual(carrier_for("94GL"), "GLS")   # (poprawiony klucz: było błędnie 92GL)

    def test_case_insensitive(self):
        self.assertEqual(carrier_for("94gl"), "GLS")

    def test_unknown_and_blank_fall_back_to_geis(self):
        self.assertEqual(carrier_for("ZZZ"), "GEIS")
        self.assertEqual(carrier_for(""), "GEIS")
        self.assertEqual(carrier_for(None), "GEIS")

    def test_theme_for_returns_full_dict(self):
        t = theme_for("94GL")
        self.assertEqual(t["carrier_label"], "GLS")
        self.assertIn("header_bg", t)
        self.assertIn("app_bar_bg", t)

    def test_zone_label_override(self):
        # 92T3 = odbiór własny: etykieta per strefa, kolory zostają z grupy BUS.
        t = theme_for("92t3")
        self.assertEqual(t["carrier_label"], "ODB. WŁASNY")
        self.assertEqual(t["header_bg"], theme_for("92JU")["header_bg"])
        # 92JU bez nadpisania — dalej BUS.
        self.assertEqual(theme_for("92JU")["carrier_label"], "BUS")

    def test_theme_for_carrier_none_when_unknown(self):
        self.assertIsNone(theme_for_carrier(""))
        self.assertIsNone(theme_for_carrier("NOPE"))
        self.assertIsNotNone(theme_for_carrier("gls"))


class ShipmentTypeTests(SimpleTestCase):
    def _hu(self, is_priority=False, category="", is_vip=False, with_customer=True):
        cust = SimpleNamespace(category=category, is_vip=is_vip, name="X") if with_customer else None
        return SimpleNamespace(is_priority=is_priority,
                               shipment=SimpleNamespace(customer=cust))

    def test_priority_wins(self):
        self.assertEqual(shipment_type_for(self._hu(is_priority=True, category="vip")), "PILNE")

    def test_vip_by_category_or_flag(self):
        self.assertEqual(shipment_type_for(self._hu(category="vip")), "VIP")
        self.assertEqual(shipment_type_for(self._hu(is_vip=True)), "VIP")

    def test_delta(self):
        self.assertEqual(shipment_type_for(self._hu(category="delta")), "DELTA")

    def test_export_and_blank_are_standard(self):
        self.assertEqual(shipment_type_for(self._hu(category="export")), "STANDARD")
        self.assertEqual(shipment_type_for(self._hu(category="")), "STANDARD")

    def test_no_customer_is_standard(self):
        self.assertEqual(shipment_type_for(self._hu(with_customer=False)), "STANDARD")
