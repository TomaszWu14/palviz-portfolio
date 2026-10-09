"""Testy charakteryzacyjne widoku ``planner_shipments_import`` (CODE-001).

Przypinają AKTUALNE zachowanie importu przesyłek z plików CSV/XLSX: stan bazy
(przesyłki, linie, odbiorcy), komunikaty i przekierowanie — strażnik przed refaktorem
(rozbicie funkcji CC=57). Oczekiwane wartości leżą w
``data/shipments_import_snapshot.json``; PK są pomijane (sekwencje PostgreSQL w CI nie
resetują się między testami) — linie wiążemy z przesyłką po nazwie, a klienta po kodzie.
Regeneracja (TYLKO świadomie, gdy zmiana zachowania jest zamierzona):
``SHIP_IMPORT_UPDATE_SNAPSHOT=1 python manage.py test transport.tests.test_shipments_import_characterization``
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

from transport.models_shipment import Shipment, ShipmentLine
from ui.models import Customer, Product
from ui.roles import GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_TRANSPORT

SNAPSHOT_FILE = Path(__file__).parent / "data" / "shipments_import_snapshot.json"
_UPDATE = os.environ.get("SHIP_IMPORT_UPDATE_SNAPSHOT") == "1"
_collected = {}

URL_NAME = "ui:planner_shipments_import"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Pełny układ eksportu SAP („Dost.”-prefiksowane nagłówki + kraj + liczba HU).
SAP_HDR = ["Dokument", "Produkt", "Ilość", "Jednostka miary", "Dost.Odbiorca materiałów",
           "Dost.Opis odbiorcy materiałów", "Miejscowość", "Dost.Kod pocztowy",
           "Dost.Klucz kraju/regionu", "Dost.Autor", "Mail", "Wymagania klienta",
           "Dost.Liczba jednostek obsługi"]


def _csv(headers, rows, delim=";"):
    lines = [delim.join(headers)] + [delim.join(str(c) for c in r) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _xlsx(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _norm(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


_SHIP_FIELDS = ("name", "status", "notes", "author", "author_email", "recipient_name",
                "customer__code", "customer__kind", "client_requirements", "actual_hu_count",
                "transport_modes", "load_mode", "destination_country", "destination_city",
                "destination_postal")
_LINE_FIELDS = ("product__code", "product_code", "quantity", "unit", "source_unit", "order",
                "notes")
_CUST_FIELDS = ("code", "kind", "name", "city", "postal")


def _db_state():
    """Stan bazy bez PK — linie przypięte do przesyłki przez jej nazwę (+ kolejność)."""
    ships = [dict(zip(_SHIP_FIELDS, r, strict=True)) for r in
             Shipment.objects.order_by("name", "created_at", "id").values_list(*_SHIP_FIELDS)]
    lines = [dict(zip(("shipment",) + _LINE_FIELDS, r, strict=True)) for r in
             ShipmentLine.objects.order_by("shipment__name", "shipment__created_at",
                                           "shipment__id", "order")
             .values_list("shipment__name", *_LINE_FIELDS)]
    custs = [dict(zip(_CUST_FIELDS, r, strict=True)) for r in
             Customer.objects.order_by("code", "kind", "name").values_list(*_CUST_FIELDS)]
    # Kolejność nazw niezależna od collation bazy (Postgres en_US ≠ SQLite binarnie);
    # sort stabilny, więc w obrębie nazwy zostaje porządek created_at/id/order z bazy.
    ships.sort(key=lambda s: s["name"])
    lines.sort(key=lambda ln: ln["shipment"])
    return _norm({"shipments": ships, "lines": lines, "customers": custs})


class ShipmentsImportCharacterizationTests(TestCase):
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

    @classmethod
    def setUpTestData(cls):
        for code in ("P1", "P2", "P3", "DMOM10001"):
            Product.objects.create(code=code, name=f"Produkt {code}")

    def setUp(self):
        u = get_user_model().objects.create_user(username="ship-char", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_TRANSPORT)[0])
        self.client.force_login(u)

    # ── narzędzia ──────────────────────────────────────────────────────────
    def _post(self, files=(), **extra):
        """files: lista (nazwa, bajty[, content_type])."""
        ups = [SimpleUploadedFile(f[0], f[1], content_type=f[2] if len(f) > 2 else "text/csv")
               for f in files]
        data = dict(extra)
        if ups:
            data["file"] = ups
        resp = self.client.post(reverse(URL_NAME), data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("ui:planner_shipments"))
        msgs = [[m.level_tag, m.message] for m in get_messages(resp.wsgi_request)]
        return {"redirect": "planner_shipments", "messages": msgs}

    def _check(self, name, result):
        got = dict(result, db=_db_state())
        if _UPDATE:
            _collected[name] = got
            return
        expected = json.loads(SNAPSHOT_FILE.read_text("utf-8"))[name]
        self.assertEqual(got, expected)

    # ── walidacja wejścia / uprawnienia ────────────────────────────────────
    def test_no_file(self):
        self._check("no_file", self._post())

    def test_one_of_files_too_large_rejects_all(self):
        ok = _csv(["Dokument", "Produkt", "Ilość", "JS"], [["D1", "P1", "5", "KAR"]])
        self._check("too_large", self._post([("a.csv", ok), ("big.csv", b"x" * (10 * 1024 * 1024 + 1))]))

    def test_header_only_files(self):
        self._check("no_data", self._post([("a.csv", b"Dokument;Produkt;Ilosc;JS\n"),
                                           ("b.csv", b"\n\n")]))

    def test_broken_xlsx_generic_error(self):
        self._check("broken_xlsx", self._post([("x.xlsx", b"not-a-zip", XLSX)]))

    def test_error_mid_import_rolls_back_everything(self):
        body = _csv(SAP_HDR, [["D1", "P1", "1", "KAR", "NEWC", "Nowy", "M", "00-001", "PL",
                               "", "", "", ""],
                              ["D2", "P2", "1", "KAR", "", "", "", "", "", "", "", "", ""]])
        real = ShipmentLine.objects.create
        calls = []

        def boom(**kw):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("awaria")
            return real(**kw)

        with mock.patch.object(ShipmentLine.objects, "create", side_effect=boom), \
                self.assertLogs("transport.views", level="ERROR"):
            res = self._post([("a.csv", body)])
        self._check("rollback", res)

    def test_guard_and_method(self):
        url = reverse(URL_NAME)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)          # anon → logowanie
        md = get_user_model().objects.create_user(username="md")
        md.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(md)
        self.assertEqual(self.client.post(url).status_code, 403)          # MD bez Transportu
        admin = get_user_model().objects.create_user(username="adm")
        admin.groups.add(Group.objects.get_or_create(name=GROUP_ADMIN)[0])
        self.client.force_login(admin)
        self.assertEqual(self.client.post(url).status_code, 302)          # admin przechodzi
        self.assertFalse(Shipment.objects.exists())

    # ── formaty / nagłówki ─────────────────────────────────────────────────
    def test_basic_by_document_units_and_quantities(self):
        # Naprawione: bez konsolidacji komunikat bez podwójnej spacji („przesyłek (13 linii)”).
        rows = [
            ["D1", "P1", "10", "KAR"], ["D1", "P2", "1,5", "krt"], ["D1", "P1", "5", "KAR"],
            ["D1", "P3", "2", "PAL"],
            ["D2", "P1", "3", "CTN"], ["D2", "P2", "4", "PAZ"], ["D2", "P3", "7", "OP"],
            ["D3", "P1", "1", "CS"], ["D3", "P2", "1", "BOX"], ["D3", "P3", "1", "PLT"],
            ["D4", "P1", "1", "PL"], ["D4", "P2", "1", ""], ["D4", "P3", "1", "szt"],
            # ilość 0 / ujemna / nieliczbowa → pomijana; dokument bez linii → brak przesyłki
            ["D5", "P1", "0", "KAR"], ["D5", "P2", "-3", "KAR"], ["D5", "P3", "abc", "KAR"],
            # pusty numer dokumentu → „bez numeru”
            ["", "P1", "2", "KAR"],
            # nieznane kody (ponad 10 → komunikat ucina listę do 10, posortowaną)
            *[["D6", f"X{i:02d}", "1", "KAR"] for i in range(12)],
            ["D6", "", "1", "KAR"],
        ]
        self._check("basic", self._post([("d.csv", _csv(["Dokument", "Produkt", "Ilość", "JS"], rows))]))

    def test_comma_delimited_english_headers(self):
        body = _csv(["Delivery", "SKU", "Qty", "Unit", "Email", "Author", "City", "ZIP", "Country"],
                    [["E1", "P1", "4", "CTN", "a@b.pl", "Ann", "Berlin", "10115", "de"]], delim=",")
        self._check("comma_english", self._post([("e.csv", body)]))

    def test_positional_fallback_for_unknown_headers(self):
        body = _csv(["A", "B", "C", "D"], [["F1", "P1", "3", "PAL"], ["F1", "P2", "2", "X"]])
        self._check("positional", self._post([("f.csv", body)]))

    def test_xlsx_numeric_cells(self):
        body = _xlsx(SAP_HDR, [[81772168, "P1", 600, "SZT", 11132232, "Nordmed ehf.", "Rvk",
                                203, "IS", "ADEMOWY", None, None, 2],
                               [81772168, "P2", 2.5, "KAR", 11132232, None, None, None, None,
                                None, None, None, 3]])
        self._check("xlsx_numeric", self._post([("s.xlsx", body, XLSX)]))

    def test_full_sap_layout_fields_and_truncation(self):
        long = "N" * 250
        rows = [
            # HU per dokument = max (z przecinkiem dziesiętnym → int)
            ["D1", "P1", "10", "KAR", "C100", "Klient Sto", "Warszawa", "00-001", "pl",
             "Jan", "jan@x.pl", "Paleta EURO", "2"],
            ["D1", "P2", "5", "KAR", "", "", "", "", "", "", "", "", "3,7"],
            # numer odbiorcy ze spacją → ignorowany (brak klienta); nazwa równa numerowi
            # (po tej samej normalizacji) → recipient_name pusty. Kraj „DEU” (ISO-3)
            # mapowany na „DE” (tabela ISO-3 → ISO-2, nie ucinanie).
            ["D2", "P1", "1", "KAR", "AB 12", "AB 12", "Kraków", "30-001", "DEU", "", "", "", "x"],
            # nazwa równa numerowi → recipient_name pusty; klient nazwany numerem
            ["D3", "P1", "1", "KAR", "C300", "C300", "", "", "", "", "", "", ""],
            # przycinanie długich pól
            ["D4", "P1", "1", "KAR", "K" * 45, long, "M" * 120, "9" * 25, "PL", "A" * 130,
             "m" * 210, "R" * 310, "1"],
        ]
        self._check("sap_full", self._post([("s.csv", _csv(SAP_HDR, rows))]))

    def test_postal_code_column_before_product(self):
        # Naprawione: wykrywanie kolumn niezależne od kolejności — „Kod pocztowy” przed
        # „Produkt” trafia do pola kodu pocztowego, a produkt do „Produkt”.
        body = _csv(["Dokument", "Kod pocztowy", "Produkt", "Ilość", "JS"],
                    [["G1", "00-001", "P1", "5", "KAR"]])
        self._check("postal_before_product", self._post([("g.csv", body)]))

    def test_multi_file_different_layouts(self):
        a = _csv(["Dokument", "Produkt", "Ilość", "JS"], [["M1", "P1", "1", "KAR"]])
        b = _xlsx(["Ilość", "Produkt", "Dokument", "JS", "Autor"], [[2, "P2", "M2", "PAL", "Ola"]])
        self._check("multi_file", self._post([("a.csv", a), ("b.xlsx", b, XLSX)]))

    # ── tworzenie vs podpinanie odbiorców, ponowny import ──────────────────
    def test_existing_customers_linked_new_created(self):
        Customer.objects.create(code="C1", kind="consignee", name="Istniejący", city="A")
        Customer.objects.create(code="C2", kind="customer", name="Klient (inny typ)")
        rows = [
            ["H1", "P1", "1", "KAR", "C1", "Nowa nazwa", "B", "11-111", "", "", "", "", ""],
            ["H2", "P1", "1", "KAR", "C2", "X", "", "", "", "", "", "", ""],
            ["H3", "P1", "1", "KAR", "C3", "Trzeci", "Łódź", "90-001", "", "", "", "", ""],
            ["H4", "P1", "1", "KAR", "C3", "Trzeci", "Łódź", "90-001", "", "", "", "", ""],
        ]
        self._check("customers", self._post([("c.csv", _csv(SAP_HDR, rows))]))

    def test_reimport_creates_duplicates(self):
        body = _csv(SAP_HDR, [["R1", "P1", "1", "KAR", "C9", "Dziewiąty", "", "", "", "", "",
                               "", ""]])
        first = self._post([("r.csv", body)])
        second = self._post([("r.csv", body)])
        self._check("reimport", {"first": first, "second": second})

    # ── konsolidacja + tryby transportu ────────────────────────────────────
    def test_consolidate_by_recipient_number_and_name(self):
        rows = [
            ["K1", "P1", "10", "KAR", "C50", "Pięćdziesiąt", "W", "00-050", "PL", "Ewa",
             "e@x.pl", "Wymóg A", "2"],
            ["K1", "P1", "1", "KAR", "C50", "", "", "", "", "", "", "", "2"],
            ["K2", "P1", "5", "KAR", "C50", "", "", "", "", "", "", "Wymóg B", "3"],
            ["K2", "P2", "5", "PAL", "C50", "", "", "", "", "", "", "", "1"],
            # bez numeru → klucz z (nazwa, miasto, kod)
            ["K3", "P1", "1", "KAR", "", "Bez Numeru", "Gdańsk", "80-001", "", "", "", "", "1"],
            ["K4", "P2", "2", "KAR", "", "Bez Numeru", "Gdańsk", "80-001", "", "", "", "", ""],
            ["K5", "P3", "3", "KAR", "", "Bez Numeru", "Gdynia", "81-001", "", "", "", "", ""],
            # numer ze spacją → też klucz z nazwy
            ["K6", "P3", "1", "KAR", "Z 1", "Spacja", "", "", "", "", "", "", ""],
            # wszystko puste → „odbiorca”
            ["", "P3", "1", "KAR", "", "", "", "", "", "", "", "", ""],
        ]
        self._check("consolidate", self._post([("k.csv", _csv(SAP_HDR, rows))], consolidate="on"))

    def test_consolidate_flag_values_and_modes(self):
        body = _csv(["Dokument", "Produkt", "Ilość", "JS"], [["T1", "P1", "1", "KAR"],
                                                             ["T2", "P2", "1", "KAR"]])
        res = {}
        for val in ("1", "true", "yes"):
            Shipment.objects.all().delete()
            res[val] = self._post([("t.csv", body)], consolidate=val,
                                  modes=["mega", "bogus", "cont20"], load_mode="loose")
            res[val]["db"] = _db_state()
        Shipment.objects.all().delete()
        self._check("flags_modes", {"runs": res, "last": self._post(
            [("t.csv", body)], modes=["naczepa"], load_mode="other")})
