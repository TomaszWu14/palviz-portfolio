"""Import danych materiałowych SAP (eksport BW „SAP_Dane_materialowe”) → MaterialMaster:
arkusze po nazwie, łączenie po MATNR, zera SAP = brak opakowania, upsert, uprawnienia."""
import io

import openpyxl
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ui.models import MaterialMaster
from ui.roles import GROUP_MASTER_DATA
from ui.sap_materials import norm_matnr, parse_workbook, upsert

M1, M2 = "000000000001005796", "000000000001000471"


def _workbook(sheets=("Nazwy", "Hierarchia produktów", "Przeliczniki")):
    wb = openpyxl.Workbook()
    wb.active.title = "Admin"
    data = {
        "Nazwy": [["MATNR", "RODZAJ", "HIERARCHIA", "REF", "TXT_SHORT_PL"],
                  [M1, "HAWA", "03060101", "ZZ-80/CH24", "Zgłębnik żołądkowy"],
                  [M2, "HAWA", "01010101", "ŻELE500", "Żel 500"]],
        "Hierarchia produktów": [["H1", "H2", "H3", "H4", "REF", "MATNR", "PRODUCENT_NAZWA", "KOD_CN",
                                  "GRUPA_ZAOPATRZENIOWA"],
                                 ["03: SPRZĘT JEDNORAZOWEGO UŻYTKU", "0306: X", "030601: Y", "03060101: Z",
                                  "ZZ-80/CH24", M1, "ACME", "90183900", "T03"]],
        "Przeliczniki": [["MATNR", "REF", "JP", "SZT_OP", "OPZ", "KAR", "PAZ", "OBJ_SZT_OP", "OBJ_KAR",
                          "OBJ_PAZ", "WAGA_SZT_OP", "WAGA_KAR", "WAGA_PAZ"],
                         [M1, "ZZ-80/CH24", "SZT", 1, 15, 300, 2400, 0.292, 87.527, 0, 0.034, 10.12, 0],
                         [M2, "ŻELE500", "SZT", 1, None, None, None, 0.828, None, None, 0.535, None, None]],
    }
    for name in sheets:
        ws = wb.create_sheet(name)
        for row in data[name]:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class ParseTests(SimpleTestCase):
    def test_norm_matnr(self):
        self.assertEqual(norm_matnr(M1), "1005796")
        self.assertEqual(norm_matnr(1005796.0), "1005796")
        self.assertEqual(norm_matnr(None), "")

    def test_sheets_joined_by_matnr_and_zero_means_no_package(self):
        m = parse_workbook(_workbook())
        self.assertEqual(set(m), {"1005796", "1000471"})
        a = m["1005796"]
        self.assertEqual((a["ref"], a["name"], a["h1"], a["producer"]),
                         ("ZZ-80/CH24", "Zgłębnik żołądkowy", "03: SPRZĘT JEDNORAZOWEGO UŻYTKU", "ACME"))
        self.assertEqual((a["pcs_per_opz"], a["pcs_per_carton"], a["pcs_per_pallet"]), (15.0, 300.0, 2400.0))
        self.assertEqual((a["vol_carton_dm3"], a["vol_pallet_dm3"], a["weight_carton_kg"]), (87.527, None, 10.12))
        self.assertNotIn("h1", m["1000471"])                      # bez wiersza w hierarchii
        self.assertIsNone(m["1000471"]["pcs_per_carton"])

    def test_missing_sheets_rejected_in_polish(self):
        with self.assertRaisesRegex(ValueError, "brak arkuszy: Przeliczniki"):
            parse_workbook(_workbook(("Nazwy", "Hierarchia produktów")))


class ImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.md = User.objects.create_user("md-sap", password="x")
        cls.md.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        cls.plain = User.objects.create_user("plain-sap", password="x")

    def _post(self, buf, name="SAP_Dane_materialowe.xlsm"):
        f = SimpleUploadedFile(name, buf.getvalue())
        return self.client.post(reverse("ui:excel_import_sap_materials"), {"file": f}, follow=True)

    def test_upsert_is_idempotent(self):
        m = parse_workbook(_workbook())
        self.assertEqual(upsert(m), (2, 0))
        m["1005796"]["name"] = "Nowa nazwa"
        self.assertEqual(upsert(m), (0, 2))
        self.assertEqual(MaterialMaster.objects.count(), 2)
        self.assertEqual(MaterialMaster.objects.get(matnr="1005796").name, "Nowa nazwa")

    def test_view_imports_and_reports(self):
        self.client.force_login(self.md)
        r = self._post(_workbook())
        self.assertContains(r, "2 materiałów (2 nowych, 0 zaktualizowanych)")
        self.assertContains(r, "z hierarchią 1")
        self.assertEqual(MaterialMaster.objects.get(matnr="1005796").pcs_per_pallet, 2400.0)

    def test_view_rejects_wrong_file_and_missing_sheets(self):
        self.client.force_login(self.md)
        self.assertContains(self._post(io.BytesIO(b"x"), "dane.csv"), "Oczekiwano pliku .xlsm lub .xlsx")
        self.assertContains(self._post(_workbook(("Nazwy",))), "brak arkuszy")
        self.assertFalse(MaterialMaster.objects.exists())

    def test_requires_master_data_role(self):
        self.client.force_login(self.plain)
        self._post(_workbook())
        self.assertFalse(MaterialMaster.objects.exists())

    def test_import_card_on_excel_page(self):
        self.client.force_login(self.md)
        r = self.client.get(reverse("ui:planner_excel_templates"))
        self.assertContains(r, reverse("ui:excel_import_sap_materials"))
