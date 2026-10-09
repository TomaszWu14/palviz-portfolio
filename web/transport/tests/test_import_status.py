"""BLOK F — ImportRun + panel statusu importów."""
import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import ImportRun, WarehouseSnapshot
from ui.roles import GROUP_MASTER_DATA


def _md():
    u = get_user_model().objects.create_user(username="imp", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
    return u


class ImportErrorHumanizationTests(TestCase):
    """UX #7: awaria importu nie może wylać surowego {exc} do komunikatu użytkownika."""

    def test_corrupt_file_shows_human_message(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from ui.roles import GROUP_TRANSPORT
        u = get_user_model().objects.create_user(username="tr", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_TRANSPORT)[0])
        self.client.force_login(u)
        bad = SimpleUploadedFile(
            "x.xlsx", b"to nie jest xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        r = self.client.post(reverse("ui:planner_shipments_import"), {"file": bad}, follow=True)
        body = r.content.decode()
        self.assertIn("Nie udało się zaimportować pliku", body)
        # surowy ślad techniczny (traceback/klasy wyjątków) nie może trafić do UI
        self.assertNotIn("Traceback", body)
        self.assertNotIn("BadZipFile", body)


class ImportRunTests(TestCase):
    def test_record_success_and_error(self):
        run = ImportRun.record("products", rows=12, label="plik.xlsx")
        self.assertEqual((run.status, run.row_count), ("ok", 12))
        bad = ImportRun.record("marm", error="zepsuty nagłówek")
        self.assertEqual(bad.status, "error")
        self.assertIn("zepsuty", bad.error_message)

    def test_panel_lists_sources_with_stale_flag(self):
        self.client.force_login(_md())
        ImportRun.record("products", rows=5)                       # świeży
        stale = ImportRun.record("users", rows=3)                  # postarzony
        ImportRun.objects.filter(pk=stale.pk).update(
            started_at=timezone.now() - datetime.timedelta(hours=48))
        WarehouseSnapshot.objects.create(name="S", row_count=100)  # batch-source
        r = self.client.get(reverse("ui:admin_import_status"))
        self.assertEqual(r.status_code, 200)
        rows = {row["label"]: row for row in r.context["rows"]}
        self.assertFalse(rows["Produkty (pełny szablon)"]["stale"])
        self.assertTrue(rows["Użytkownicy"]["stale"])
        self.assertTrue(rows["SAP MARM"]["stale"])                 # nigdy = nieświeży
        self.assertFalse(rows["Snapshot zajętości (SAP WMS)"]["stale"])
        self.assertEqual(rows["Snapshot zajętości (SAP WMS)"]["count"], 100)

    def test_import_paths_write_a_run(self):
        """Reprezentatywna ścieżka (import klientów) zostawia ślad ImportRun."""
        import io
        import openpyxl
        from django.core.files.uploadedfile import SimpleUploadedFile
        from ui.roles import GROUP_ADMIN
        u = _md()
        u.groups.add(Group.objects.get_or_create(name=GROUP_ADMIN)[0])
        self.client.force_login(u)
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(["Klient", "Nazwa 1"]); ws.append(["K-1", "Testowy odbiorca"])
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        self.client.post(reverse("ui:planner_customer_import"),
                         {"file": SimpleUploadedFile("klienci.xlsx", buf.read())})
        self.assertTrue(ImportRun.objects.filter(kind="customers").exists())
