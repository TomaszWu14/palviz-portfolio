"""Testy charakteryzacyjne widoku ``warehouse_map_upload`` (CODE-001).

Przypinają AKTUALNE zachowanie importu snapshotu SAP WMS / EWM (xlsx i csv): stan bazy
(snapshoty + wiersze lokalizacji), komunikaty i przekierowanie — strażnik przed
refaktorem (rozbicie funkcji CC=63). Oczekiwane wartości leżą w
``data/warehouse_map_upload_snapshot.json``; PK są normalizowane (sekwencje PostgreSQL
w CI nie resetują się między testami) — wiersze wiążemy ze snapshotem po nazwie, a
przekierowanie na szczegóły zapisujemy jako ``detail:<nazwa snapshotu>``.
Regeneracja (TYLKO świadomie, gdy zmiana zachowania jest zamierzona):
``WH3D_UPLOAD_UPDATE_SNAPSHOT=1 python manage.py test wh3d.tests.test_warehouse_map_upload_characterization``
"""
import io
import json
import os
from pathlib import Path

import openpyxl
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import WarehouseSnapshot, WarehouseSnapshotRow
from ui.roles import GROUP_MASTER_DATA

SNAPSHOT_FILE = Path(__file__).parent / "data" / "warehouse_map_upload_snapshot.json"
_UPDATE = os.environ.get("WH3D_UPLOAD_UPDATE_SNAPSHOT") == "1"
_collected = {}

URL_NAME = "ui:warehouse_map_upload"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Pełny nagłówek eksportu SAP WMS (miejsca składowania) — wszystkie kolumny _SAP_COLS.
SAP_HEADER = ["Miejsce składowania", "Typ magazynu", "Sekcja magazynu", "Podz. miej. skład.",
              "Puste miejsce skład.", "Blok. wyd. z magaz.", "Blokada um. w magaz.",
              "Całkowite zdolności", "Przej. w miej. sk.", "Stos miejsca skład.",
              "Poziom miejsca skł."]


def _xlsx(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csv(headers, rows, bom=True):
    lines = [",".join(headers)] + [",".join(str(c) for c in r) for r in rows]
    text = "\n".join(lines) + "\n"
    return (("﻿" if bom else "") + text).encode("utf-8")


def _norm(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


_ROW_FIELDS = ("location_code", "warehouse_type", "section", "storage_group", "is_empty",
               "blocked_pick", "blocked_put", "capacity_mm", "zone", "aisle", "stack",
               "col_code", "level", "col_idx")


def _db_state():
    """Stan bazy bez PK — wiersze przypięte do snapshotu przez jego nazwę."""
    snaps = [{"name": s.name, "row_count": s.row_count, "occupied_count": s.occupied_count,
              "blocked_count": s.blocked_count}
             for s in WarehouseSnapshot.objects.order_by("name", "uploaded_at")]
    rows = [dict(zip(("snapshot",) + _ROW_FIELDS, r, strict=True)) for r in
            WarehouseSnapshotRow.objects.order_by("snapshot__name", "location_code", "stack",
                                                  "col_code", "level")
            .values_list("snapshot__name", *_ROW_FIELDS)]
    return _norm({"snapshots": snaps, "rows": rows})


class WarehouseMapUploadCharacterizationTests(TestCase):
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
        u = get_user_model().objects.create_user(username="wh-char", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    # ── narzędzia ──────────────────────────────────────────────────────────
    def _post(self, content=None, filename="s.csv", name="S", ctype="text/csv"):
        data = {"name": name} if name is not None else {}
        if content is not None:
            data["file"] = SimpleUploadedFile(filename, content, content_type=ctype)
        resp = self.client.post(reverse(URL_NAME), data)
        self.assertEqual(resp.status_code, 302)
        loc = resp["Location"]
        if loc == reverse("ui:warehouse_map"):
            target = "map"
        else:
            snap = WarehouseSnapshot.objects.latest("uploaded_at", "id")
            self.assertEqual(loc, reverse("ui:warehouse_map_detail", kwargs={"pk": snap.pk}))
            target = f"detail:{snap.name}"
        msgs = [[m.level_tag, m.message] for m in get_messages(resp.wsgi_request)]
        return {"redirect": target, "messages": msgs}

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

    def test_file_too_large(self):
        self._check("too_large", self._post(b"x" * (10 * 1024 * 1024 + 1)))

    def test_file_at_size_limit_is_processed(self):
        # Dokładnie 10 MB przechodzi bramkę rozmiaru (warunek „>”), same puste wiersze.
        body = _csv(["Miejsce składowania"], [])
        body += b"\n" * (10 * 1024 * 1024 - len(body))
        self._check("at_size_limit", self._post(body))

    def test_unsupported_extension(self):
        self._check("bad_ext", self._post(b"a,b\n1,2\n", filename="plik.txt"))

    def test_broken_xlsx_reports_read_error(self):
        res = self._post(b"not-a-zip", filename="plik.xlsx", ctype=XLSX)
        self.assertEqual(len(res["messages"]), 1)
        self.assertEqual(res["messages"][0][0], "error")
        self.assertTrue(res["messages"][0][1].startswith("Błąd odczytu pliku: "))
        self._check("broken_xlsx", dict(res, messages=[]))

    def test_legacy_xls_goes_through_openpyxl_and_fails(self):
        # .xls jest reklamowany, ale czytany openpyxl-em (tylko OOXML) — prawdziwy BIFF
        # zawsze kończy się błędem odczytu. Obecne zachowanie, przypięte.
        res = self._post(b"\xd0\xcf\x11\xe0 fake biff", filename="plik.xls")
        self.assertEqual(res["redirect"], "map")
        self.assertTrue(res["messages"][0][1].startswith("Błąd odczytu pliku: "))
        self.assertFalse(WarehouseSnapshot.objects.exists())

    def test_csv_not_utf8(self):
        res = self._post("Miejsce składowania\nB0-01-100A\n".encode("cp1250"))
        self.assertTrue(res["messages"][0][1].startswith("Błąd odczytu pliku: "))
        self._check("csv_not_utf8", dict(res, messages=[]))

    def test_csv_row_with_extra_field_breaks_read(self):
        # Nadmiarowe pole → DictReader daje klucz None → None.strip() → „Błąd odczytu pliku”.
        # Obecne zachowanie (cały plik odrzucony przez jeden wiersz), przypięte.
        self._check("csv_extra_field",
                    self._post(_csv(["Miejsce składowania"], [["B0-01-100A"], ["B0-01-101A,X"]])))

    def test_no_recognised_locations_rolls_back(self):
        self._check("no_locations", self._post(_csv(["Coś innego", "Ilość"], [["a", "1"]])))

    def test_guard_and_method(self):
        url = reverse(URL_NAME)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)          # anon → logowanie
        self.client.force_login(get_user_model().objects.create_user(username="nobody"))
        self.assertEqual(self.client.post(url).status_code, 403)          # bez roli
        self.assertFalse(WarehouseSnapshot.objects.exists())

    # ── pełny eksport SAP (xlsx) ───────────────────────────────────────────
    def test_sap_xlsx_full_columns(self):
        long_grp = "G" * 60
        rows = [
            # 3-częściowy regał, kolumna „Puste” = X → wolne; poziom/stos z kolumn SAP.
            ["B0-01-100A", "RT", "S1", "GR1", "X", None, None, 1500, "1", "100", "2"],
            # Puste pole w kolumnie „Puste” = ZAJĘTE; blokada wydania „x”.
            ["b0-01-100b", "RT", "S1", "GR1", None, "x", None, "1200.9", None, None, None],
            # Blokada umieszczenia + pojemność nienumeryczna → 0; poziom nienumeryczny.
            ["B0-02-200C", "RT", "S2", long_grp, None, None, "TAK", "abc", None, None, "abc"],
            # 4-częściowy z jawnym poziomem 3B (ma pierwszeństwo przed kolumną poziomu).
            ["B0-01-300-3B", "RT", "S1", "", "X", None, None, None, None, None, "4"],
            # Lokalizacje dzielone 300C-1 / 300C-2 (sub-slot cyfrą).
            ["B0-01-301C-1", "RT", "S1", "", None, None, None, None, None, None, None],
            ["B0-01-301C-2", "RT", "S1", "", "X", None, None, None, None, None, None],
            # 4-częściowy bez wzorca liczba+litera → col_code = ostatni znak.
            ["B0-01-302-AB1", "RT", "S1", "", None, None, None, None, None, None, None],
            # Kolumny X/Y/Z → poziomy 2/3/4 (schemat pozycyjny legacy).
            ["B0-03-100X", "RT", "", "", None, None, None, None, None, None, None],
            ["B0-03-100Y", "RT", "", "", "x", None, None, None, None, None, None],
            ["B0-03-100Z", "RT", "", "", "", None, None, None, None, None, None],
            # Blok/zewnętrzne: jednoczęściowa nazwa kończąca się literą „Y” dostaje
            # col_code Y → poziom 3 (quirk), aleja „00”; kod liczbowy bez litery.
            ["ZWROTY", "92EX", "SEKCJA-DLUGA-PONAD-20-ZNAKOW", "", None, None, None,
             None, None, None, None],
            ["04.01", "TYP-ZA-DLUGI-X", "", "", "X", "X", "X", 900, None, None, None],
            # Przejście w miej. sk. jako liczba → str().zfill.
            ["B0-5-400A", "RT", "", "", None, None, None, None, 7, None, None],
            # Wiersz bez lokalizacji — pomijany po cichu (komunikat i tak mówi „0 pominiętych”).
            [None, "RT", "S1", "", None, None, None, None, None, None, None],
            # Całkowicie pusty wiersz — pomijany na etapie odczytu xlsx.
            [None] * 11,
        ]
        self._check("sap_xlsx_full", self._post(_xlsx(SAP_HEADER, rows), filename="Eksport.XLSX",
                                                name="  ", ctype=XLSX))

    def test_xlsx_blank_header_cells(self):
        # Pusta komórka nagłówka → klucz "" (ignorowany); stos z kolumny SAP.
        rows = [["B0-01-100A", "x", "150"], ["B0-01-101", "y", None]]
        self._check("xlsx_blank_header",
                    self._post(_xlsx(["Miejsce składowania", None, "Stos miejsca skład."], rows),
                               filename="h.xlsx", name="Nagłówki", ctype=XLSX))

    # ── format prosty / stock-at-location (csv) ────────────────────────────
    def test_csv_simple_occupancy_fallbacks(self):
        # Brak kolumny „Puste” → zajętość z HU / produktu / ilości.
        header = ["Location", "HU", "Product", "Qty"]
        rows = [
            ["B0-01-100A", "11111717", "", ""],       # HU → zajęte
            ["B0-01-101A", "", "SKU-1", ""],          # produkt → zajęte
            ["B0-01-102A", "", "", "5"],              # ilość > 0 → zajęte
            ["B0-01-103A", "", "", "0"],              # ilość 0 → wolne
            ["B0-01-104A", "", "", "abc"],            # ilość nienumeryczna → wolne
            ["B0-01-105A", "", "", ""],               # nic → wolne
            ["B0-01-106A", "  ", "  ", ""],           # same spacje → wolne
            ["B0-01-107", "", "", "1"],               # stos bez litery
            ["", "999", "", ""],                      # brak lokalizacji → pominięty
        ]
        # UWAGA (obecne zachowanie): wiersz bez lokalizacji jest pomijany, a komunikat
        # i tak podaje „0 pominiętych” (licznik zaszyty na sztywno).
        self._check("csv_simple", self._post(_csv(header, rows), filename="stock.CSV",
                                             name="Stock CSV"))

    def test_csv_without_bom_and_default_name(self):
        self._check("csv_no_bom",
                    self._post(_csv(["lokalizacja"], [["B0-07-700A"]], bom=False),
                               filename="plik.csv", name=None))

    def test_bulk_chunking_over_5000_rows(self):
        rows = [[f"B0-{a:02d}-{s}A"] for a in range(1, 7) for s in range(100, 1000)]
        self.assertGreater(len(rows), 5000)
        res = self._post(_csv(["Miejsce składowania"], rows), name="Duży")
        snap = WarehouseSnapshot.objects.get(name="Duży")
        self.assertEqual(snap.rows.count(), len(rows))
        self._check_summary("bulk_chunking", res, snap)

    def _check_summary(self, name, res, snap):
        got = {**res, "snapshot": {"row_count": snap.row_count,
                                   "occupied_count": snap.occupied_count,
                                   "blocked_count": snap.blocked_count}}
        if _UPDATE:
            _collected[name] = _norm(got)
            return
        self.assertEqual(_norm(got), json.loads(SNAPSHOT_FILE.read_text("utf-8"))[name])

    def test_each_upload_creates_new_snapshot(self):
        # Brak „aktualizacji”: ten sam plik wgrany dwa razy = dwa niezależne snapshoty.
        body = _csv(["Miejsce składowania", "Puste miejsce skład."],
                    [["B0-01-100A", "X"], ["B0-01-100B", ""]])
        first = self._post(body, name="Powtórka")
        # Komunikaty pierwszego żądania nie zostały „skonsumowane” przez szablon, więc
        # drugie żądanie widzi oba sukcesy (artefakt klienta testowego, nie widoku).
        second = self._post(body, name="Powtórka")
        self._check("repeat_upload", {"first": first, "second": second})
