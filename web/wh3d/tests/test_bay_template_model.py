"""Modele części 1: szablon gniazda (walidacja), reguła rzędu, wyjątki (unikalność, ochrona szablonu)."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase

from wh3d.models import BayTemplate, LocationOverride, WarehouseModel, WarehouseModelRack


def levels(*letters, split=()):
    return [{"letter": L, "height_mm": 1000, "ewm_type": "0010", "split": L in split, "max_kg": 1000}
            for L in letters]


class BayTemplateTests(TestCase):
    def test_valid_template_and_labels(self):
        t = BayTemplate(name="3 pal.", pallets_per_beam=3, levels=levels("B", "C", "X", split=("C",)))
        t.full_clean()
        self.assertEqual(t.level_label, "B C½ X")
        self.assertEqual(t.ewm_types_label, "0010")

    def test_invalid_templates_raise_polish_errors(self):
        cases = {
            "co najmniej jeden poziom": BayTemplate(name="a", pallets_per_beam=3, levels=[]),
            "unikalne": BayTemplate(name="b", pallets_per_beam=3, levels=levels("A", "A")),
            "wysokość": BayTemplate(name="c", pallets_per_beam=3,
                                    levels=[{"letter": "A", "height_mm": 0, "ewm_type": "", "split": False}]),
            "palet": BayTemplate(name="d", pallets_per_beam=0, levels=levels("A")),
            "litera": BayTemplate(name="e", pallets_per_beam=1, levels=levels("AB")),
            "zakresie 1–10": BayTemplate(name="f", pallets_per_beam=11, levels=levels("A")),
        }
        for fragment, t in cases.items():
            with self.assertRaises(ValidationError, msg=fragment) as cm:
                t.full_clean()
            self.assertIn(fragment, " ".join(cm.exception.messages))


class RackRuleAndOverrideTests(TestCase):
    def setUp(self):
        self.wm = WarehouseModel.objects.create(name="M")
        self.rack = WarehouseModelRack.objects.create(model=self.wm, zone="B0", rack_id="07", n_bays=2)
        self.tpl = BayTemplate.objects.create(name="T", pallets_per_beam=3, levels=levels("A"))

    def test_rack_defaults_and_bay_numbers_validation(self):
        self.assertIsNone(self.rack.template)
        self.assertEqual(self.rack.bay_numbers, "")
        self.assertFalse(self.rack.reverse)
        self.rack.bay_numbers = "10-5"
        with self.assertRaises(ValidationError):
            self.rack.full_clean()
        self.rack.bay_numbers = "10-47,50"
        self.rack.full_clean()

    def test_override_unique(self):
        LocationOverride.objects.create(rack=self.rack, bay=30, letter="A", action="skip")
        with self.assertRaises(IntegrityError):
            LocationOverride.objects.create(rack=self.rack, bay=30, letter="A", action="skip")

    def test_template_used_by_override_is_protected_rack_is_set_null(self):
        self.rack.template = self.tpl
        self.rack.save()
        other = BayTemplate.objects.create(name="U", pallets_per_beam=4, levels=levels("Y"))
        LocationOverride.objects.create(rack=self.rack, bay=29, action="template", template=other)
        with self.assertRaises(ProtectedError):
            other.delete()
        self.tpl.delete()
        self.rack.refresh_from_db()
        self.assertIsNone(self.rack.template)
        self.assertEqual(self.rack.overrides.count(), 1)
