"""Testy charakteryzacyjne widoku ``planner_ref_materials_import`` (CODE-001).

Przypinają AKTUALNE zachowanie importu materiałów referencyjnych z eksportu SAP MARM
(XLSX): stan bazy (``MaterialReference`` + ślad ``ImportRun``), komunikaty i
przekierowanie — strażnik przed refaktorem (rozbicie funkcji CC=41). Oczekiwane
wartości leżą w ``data/ref_materials_import_snapshot.json``; PK są pomijane
(sekwencje PostgreSQL w CI nie resetują się między testami).
Regeneracja (TYLKO świadomie, gdy zmiana zachowania jest zamierzona):
``REFMAT_IMPORT_UPDATE_SNAPSHOT=1 python manage.py test ui.tests.test_ref_materials_import_characterization``
"""
import io
import json
import os
from pathlib import Path
from unittest import mock

import openpyxl
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import ImportRun, MaterialReference
from ui.roles import GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT

SNAPSHOT_FILE = Path(__file__).parent / "data" / "ref_materials_import_snapshot.json"
_UPDATE = os.environ.get("REFMAT_IMPORT_UPDATE_SNAPSHOT") == "1"
_collected = {}

URL_NAME = "ui:planner_ref_materials_import"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Układ eksportu SAP MARM zgodny z domyślnymi indeksami pozycyjnymi widoku.
SAP_HDR = ["Materiał", "Alternatywna jednostka miary", "Mianownik", "Licznik", "Jedn.",
           "Szerokość", "Wysokość", "Długość", "Jedn. wymiaru", "Waga brutto",
           "Waga netto", "Jedn. wagi", "Objętość", "Kod EAN/UPC", "Typ EAN",
           "Grupa", "Krótki tekst materiału", "Dostawca", "Szukany ciąg zn.", "Nazwa"]


def _row(mat, alt, licz=None, w=None, h=None, l=None, gross=None, ean=None,
         name=None, sup_sh=None, sup_fl=None):
    r = [None] * 20
    r[0], r[1], r[3] = mat, alt, licz
    r[5], r[6], r[7], r[9], r[13] = w, h, l, gross, ean
    r[16], r[18], r[19] = name, sup_sh, sup_fl
    return r


def _xlsx(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    if headers is not None:
        ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _norm(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


_REF_FIELDS = ("code", "name", "supplier_short", "supplier_full", "width_cm", "height_cm",
               "length_cm", "gross_kg", "pieces_per_carton", "ean")
_RUN_FIELDS = ("kind", "label", "status", "row_count", "error_message", "user__username")


def _db_state():
    refs = [dict(zip(_REF_FIELDS, r, strict=True)) for r in
            MaterialReference.objects.order_by("code").values_list(*_REF_FIELDS)]
    runs = [dict(zip(_RUN_FIELDS, r, strict=True)) for r in
            ImportRun.objects.order_by("started_at", "id").values_list(*_RUN_FIELDS)]
    return _norm({"refs": refs, "runs": runs})


class RefMaterialsImportCharacterizationTests(TestCase):
    maxDiff = None

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if _UPDATE and _collected:
            data = json.loads(SNAPSHOT_FILE.read_text("utf-8")) if SNAPSHOT_FILE.exists() else {}
            data.update(_collected)
            SNAPSHOT_FILE.parent.mkdir(exist_ok=True)
            SNAPSHOT_FILE.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False)
                                     + "\n", "utf-8")

    def setUp(self):
        u = get_user_model().objects.create_user(username="refmat-char", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    # ── narzędzia ──────────────────────────────────────────────────────────
    def _post(self, content=None, name="marm.xlsx", ctype=XLSX):
        data = {}
        if content is not None:
            data["file"] = SimpleUploadedFile(name, content, content_type=ctype)
        resp = self.client.post(reverse(URL_NAME), data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("ui:planner_ref_materials"))
        msgs = [[m.level_tag, m.message] for m in get_messages(resp.wsgi_request)]
        return {"redirect": "planner_ref_materials", "messages": msgs}

    def _check(self, name, result):
        got = dict(result, db=_db_state())
        if _UPDATE:
            _collected[name] = got
            return
        expected = json.loads(SNAPSHOT_FILE.read_text("utf-8"))[name]
        self.assertEqual(got, expected)

    # ── walidacja wejścia / uprawnienia ────────────────────────────────────
    def test_get_redirects_without_messages(self):
        resp = self.client.get(reverse(URL_NAME))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("ui:planner_ref_materials"))
        self.assertEqual(list(get_messages(resp.wsgi_request)), [])

    def test_no_file(self):
        self._check("no_file", self._post())

    def test_too_large(self):
        self._check("too_large", self._post(b"x" * (20 * 1024 * 1024 + 1)))

    def test_broken_xlsx(self):
        self._check("broken_xlsx", self._post(b"not-a-zip"))

    def test_empty_workbook(self):
        self._check("empty_workbook", self._post(_xlsx(None, [])))

    def test_header_only(self):
        self._check("header_only", self._post(_xlsx(SAP_HDR, [])))

    def test_guard(self):
        url = reverse(URL_NAME)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)            # anon → logowanie
        self.assertIn("/login/", self.client.post(url)["Location"])
        tr = get_user_model().objects.create_user(username="tr")
        tr.groups.add(Group.objects.get_or_create(name=GROUP_TRANSPORT)[0])
        self.client.force_login(tr)
        self.assertEqual(self.client.post(url).status_code, 403)            # Transport bez MD
        admin = get_user_model().objects.create_user(username="adm")
        admin.groups.add(Group.objects.get_or_create(name=GROUP_ADMIN)[0])
        self.client.force_login(admin)
        resp = self.client.post(url, {"file": SimpleUploadedFile(
            "a.xlsx", _xlsx(SAP_HDR, [_row("A1", "KAR", 6, 10, 20, 30)]), XLSX)})
        self.assertEqual(resp.status_code, 302)                             # admin przechodzi
        self.assertTrue(MaterialReference.objects.filter(code="A1").exists())

    # ── formaty / nagłówki / tworzenie ─────────────────────────────────────
    def test_full_sap_layout_create(self):
        rows = [
            # KAR z pełnymi danymi (liczby)
            _row("M1", "KAR", 12, 30.5, 20, 40, 7.25, 5901234567890, "Kubek", "ACME", "Acme Sp. z o.o."),
            # ST przed KAR — nazwa z pierwszego wiersza, potem KAR nadpisuje całość
            _row("M2", "ST", 1, name="Talerz ST", sup_sh="BETA", sup_fl="Beta SA"),
            _row("M2", "KAR", "24,0", "10", "11", "12", "3.5", "  590000  ", "Talerz KAR", "B", "B full"),
            # ST po KAR — ignorowany (materiał już jest w refs)
            _row("M1", "ST", 1, name="Inna nazwa"),
            # sam ST z nazwą → rekord tylko z nazwą/dostawcą
            _row("M3", "ST", 1, name="Tylko nazwa", sup_sh="GAM"),
            # sam ST bez nazwy → brak rekordu
            _row("M4", "ST", 1),
            # KAR z zerami / ujemnymi → None; licznik 0 → None
            _row("M5", "KAR", 0, 0, -5, 0, 0, None, None, None, None),
            # duplikat KAR — ostatni wygrywa
            _row("M6", "KAR", 6, 1, 1, 1, 1, None, "Pierwszy"),
            _row("M6", "KAR", 8, 2, 2, 2, 2, None, "Drugi"),
            # pusty materiał, pusty wiersz
            _row(None, "KAR", 6, 1, 1, 1),
            _row("   ", "KAR", 6, 1, 1, 1),
            # kod liczbowy + spacje wokół, jednostka „kar” małymi (≠ KAR)
            _row(123456, "KAR", 4, 5, 5, 5, name=" Liczbowy "),
            _row("M7", "kar", 4, 5, 5, 5, name="Małe litery"),
            # licznik nieliczbowy → cały wiersz pominięty
            _row("M8", "KAR", "abc", 1, 1, 1, name="Zły licznik"),
            # długie teksty przycinane (nazwa 250, skrót 50, pełna 500, EAN 30, kod 100)
            _row("L" * 120, "KAR", 1, 1, 1, 1, None, "9" * 40, "N" * 300, "S" * 60, "F" * 600),
        ]
        self._check("full_create", self._post(_xlsx(SAP_HDR, rows)))

    def test_comma_decimal_dimension_skips_row(self):
        # BŁĄD (przypięty, nie naprawiany): wymiar/waga jako tekst z przecinkiem („12,5”)
        # → float() rzuca → CAŁY wiersz KAR liczony jako pominięty, mimo że licznik
        # akceptuje „24,0”. Materiał nie trafia do bazy (brak innego wiersza).
        rows = [_row("C1", "KAR", 6, "12,5", 10, 10, name="Przecinek"),
                _row("C2", "KAR", 6, 10, 10, 10, "1,5", name="Waga z przecinkiem")]
        self._check("comma_dims", self._post(_xlsx(SAP_HDR, rows)))

    def test_reordered_headers_by_name(self):
        order = [16, 19, 18, 13, 9, 7, 6, 5, 3, 1, 0]
        hdr = [SAP_HDR[i] for i in order]
        src = [_row("R1", "KAR", 10, 11, 12, 13, 1.5, "590111", "Przestawione", "SH", "FULL"),
               _row("R2", "ST", 1, name="Tylko ST", sup_sh="X", sup_fl="Y")]
        rows = [[r[i] for i in order] for r in src]
        self._check("reordered", self._post(_xlsx(hdr, rows)))

    def test_unknown_headers_positional_fallback(self):
        hdr = [f"kol{i}" for i in range(20)]
        rows = [_row("P1", "KAR", 5, 1, 2, 3, 4, "111", "Poz", "S", "F")]
        self._check("positional", self._post(_xlsx(hdr, rows)))

    def test_short_header_index_errors_skip_rows(self):
        # Tylko 2 kolumny → domyślne indeksy (Licznik=3, nazwa=16…) poza zakresem wiersza.
        rows = [["S1", "KAR"], ["S2", "ST"], ["S3", "KAR"]]
        self._check("short_header", self._post(_xlsx(["Materiał", "Alternatywna jednostka miary"],
                                                     rows)))

    def test_nazwa_with_trailing_space_header(self):
        # Nagłówek „Nazwa ” jest strip()-owany → trafia jako „Nazwa”.
        hdr = list(SAP_HDR)
        hdr[19] = "Nazwa "
        rows = [_row("N1", "KAR", 2, 1, 1, 1, name="X", sup_fl="Pełna nazwa")]
        self._check("nazwa_space", self._post(_xlsx(hdr, rows)))

    # ── aktualizacja ───────────────────────────────────────────────────────
    def test_update_existing(self):
        MaterialReference.objects.create(code="U1", name="Stara", supplier_short="OLD",
                                         width_cm=99, height_cm=99, length_cm=99,
                                         gross_kg=99, pieces_per_carton=99, ean="OLD")
        MaterialReference.objects.create(code="U2", name="Stara 2", width_cm=50,
                                         pieces_per_carton=7, ean="KEEP")
        MaterialReference.objects.create(code="U3", name="Nietknięta", width_cm=1)
        rows = [_row("U1", "KAR", 3, 4, None, 6, None, None, "Nowa"),
                _row("U2", "ST", 1, name="Nowa 2", sup_sh="NS"),
                _row("U4", "KAR", 1, 1, 1, 1, name="Nowy")]
        self._check("update", self._post(_xlsx(SAP_HDR, rows), name="aktualizacja.xlsx"))

    # ── błąd zapisu → rollback ─────────────────────────────────────────────
    def test_error_mid_save_rolls_back(self):
        rows = [_row("B1", "KAR", 1, 1, 1, 1, name="Pierwszy"),
                _row("B2", "KAR", 1, 1, 1, 1, name="Drugi")]
        real = MaterialReference.objects.update_or_create
        calls = []

        def boom(**kw):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("awaria")
            return real(**kw)

        with mock.patch.object(MaterialReference.objects, "update_or_create", side_effect=boom):
            res = self._post(_xlsx(SAP_HDR, rows))
        self._check("rollback", res)

    def test_import_run_record_failure_after_commit(self):
        # BŁĄD (przypięty, nie naprawiany): awaria zapisu śladu ImportRun PO zatwierdzonej
        # transakcji daje jednocześnie „Import zakończony…” i „Błąd importu: …”, choć dane
        # są już w bazie — użytkownik dostaje sprzeczny komunikat.
        rows = [_row("I1", "KAR", 1, 1, 1, 1, name="Zapisany")]
        with mock.patch.object(ImportRun, "record", side_effect=RuntimeError("log padł")):
            res = self._post(_xlsx(SAP_HDR, rows))
        self._check("record_fail", res)
