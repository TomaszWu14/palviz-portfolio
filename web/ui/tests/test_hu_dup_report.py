"""Komenda diagnostyczna hu_dup_report (przed migracją 0107).

Na bazie z nałożonym constraintem duplikaty są niemożliwe do utworzenia przez ORM,
więc test pilnuje tego, co da się sprawdzić: komenda importuje się, wykonuje i
raportuje czystą bazę zamiast wysypać się na produkcji tuż przed migracją."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from ui.models import HandlingUnit, Shipment


class HuDupReportTests(TestCase):
    def test_reports_clean_database(self):
        sh = Shipment.objects.create(name="D1")
        HandlingUnit.objects.create(shipment=sh, seq=1, code="HU1")
        HandlingUnit.objects.create(shipment=sh, seq=2, code="HU2")
        HandlingUnit.objects.create(shipment=sh, seq=3, code="")     # puste są wyłączone

        out = StringIO()
        call_command("hu_dup_report", stdout=out)
        self.assertIn("Brak duplikatów", out.getvalue())

    def test_runs_on_empty_database(self):
        out = StringIO()
        call_command("hu_dup_report", stdout=out)
        self.assertIn("Brak duplikatów", out.getvalue())
