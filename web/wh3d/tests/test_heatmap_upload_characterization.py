"""Testy charakteryzacyjne widoku ``heatmap_upload`` (CODE-001).

Przypinają AKTUALNE zachowanie importu aktywności pickerów (xlsx i csv): wykrywanie
kolumn po nagłówkach, łączenie daty i czasu, stan bazy (partie + aktywności), komunikaty
i przekierowanie — strażnik przed refaktorem (rozbicie funkcji CC=50). Oczekiwane
wartości leżą w ``data/heatmap_upload_snapshot.json``; PK są normalizowane (sekwencje
PostgreSQL w CI nie resetują się między testami) — aktywności wiążemy z partią po nazwie,
a przekierowanie na szczegóły zapisujemy jako ``detail:<nazwa partii>``.
Regeneracja (TYLKO świadomie, gdy zmiana zachowania jest zamierzona):
``WH3D_HEATMAP_UPDATE_SNAPSHOT=1 python manage.py test wh3d.tests.test_heatmap_upload_characterization``
"""
import csv
import datetime as dt
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

from ui.roles import GROUP_MASTER_DATA
from wh3d.models import PickerActivity, PickerActivityBatch

SNAPSHOT_FILE = Path(__file__).parent / "data" / "heatmap_upload_snapshot.json"
_UPDATE = os.environ.get("WH3D_HEATMAP_UPDATE_SNAPSHOT") == "1"
_collected = {}

URL_NAME = "ui:heatmap_upload"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MB = 1024 * 1024


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


def _csv(headers, rows, bom=True, enc="utf-8"):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(headers)
    w.writerows(rows)
    text = buf.getvalue()
    return (("﻿" if bom else "") + text).encode(enc)


def _norm(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


_ACT_FIELDS = ("location_code", "confirmed_at", "picker_name", "task_type", "material_code",
               "qty", "unit", "lot")


def _db_state():
    """Stan bazy bez PK — aktywności przypięte do partii przez jej nazwę."""
    batches = [{"name": b.name, "row_count": b.row_count, "date_from": b.date_from,
                "date_to": b.date_to}
               for b in PickerActivityBatch.objects.order_by("name", "uploaded_at", "id")]
    acts = [dict(zip(("batch",) + _ACT_FIELDS, r, strict=True)) for r in
            PickerActivity.objects.order_by("batch__name", "location_code", "confirmed_at",
                                            "picker_name", "material_code", "lot")
            .values_list("batch__name", *_ACT_FIELDS)]
    return _norm({"batches": batches, "activities": acts})


class HeatmapUploadCharacterizationTests(TestCase):
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
        u = get_user_model().objects.create_user(username="hm-char", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    # ── narzędzia ──────────────────────────────────────────────────────────
    def _post(self, content=None, filename="p.csv", name="H", ctype="text/csv"):
        data = {"name": name} if name is not None else {}
        if content is not None:
            data["file"] = SimpleUploadedFile(filename, content, content_type=ctype)
        resp = self.client.post(reverse(URL_NAME), data)
        self.assertEqual(resp.status_code, 302)
        loc = resp["Location"]
        if loc == reverse("ui:heatmap_list"):
            target = "list"
        else:
            batch = PickerActivityBatch.objects.latest("uploaded_at", "id")
            self.assertEqual(loc, reverse("ui:heatmap_detail", kwargs={"pk": batch.pk}))
            target = f"detail:{batch.name}"
        msgs = [[m.level_tag, m.message] for m in get_messages(resp.wsgi_request)]
        return {"redirect": target, "messages": msgs}

    def _check(self, name, result, with_db=True):
        got = _norm(dict(result, db=_db_state()) if with_db else result)
        if _UPDATE:
            _collected[name] = got
            return
        expected = json.loads(SNAPSHOT_FILE.read_text("utf-8"))[name]
        self.assertEqual(got, expected)

    def _read_error(self, res):
        self.assertEqual(len(res["messages"]), 1)
        self.assertEqual(res["messages"][0][0], "error")
        self.assertTrue(res["messages"][0][1].startswith("Błąd odczytu pliku: "))
        self.assertEqual(res["redirect"], "list")
        self.assertFalse(PickerActivityBatch.objects.exists())

    # ── walidacja wejścia / uprawnienia ────────────────────────────────────
    def test_no_file(self):
        self._check("no_file", self._post())

    def test_file_too_large(self):
        self._check("too_large", self._post(b"x" * (20 * MB + 1)))

    def test_file_at_size_limit_is_processed(self):
        # Dokładnie 20 MB przechodzi bramkę rozmiaru (warunek „>”); puste wiersze pomijane.
        body = _csv(["lokalizacja", "data potw"], [["A1", "2026-08-01 08:00"]])
        body += b"\n" * (20 * MB - len(body))
        self._check("at_size_limit", self._post(body, name="Limit"))

    def test_guard_and_method(self):
        url = reverse(URL_NAME)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)          # anon → logowanie
        self.client.force_login(get_user_model().objects.create_user(username="nobody"))
        self.assertEqual(self.client.post(url).status_code, 403)          # bez roli
        self.assertFalse(PickerActivityBatch.objects.exists())

    def test_broken_xlsx_reports_read_error(self):
        self._read_error(self._post(b"not-a-zip", filename="plik.xlsx", ctype=XLSX))

    def test_legacy_xls_goes_through_openpyxl_and_fails(self):
        # .xls czytany openpyxl-em (tylko OOXML) — prawdziwy BIFF zawsze kończy się
        # błędem odczytu. Obecne zachowanie, przypięte.
        self._read_error(self._post(b"\xd0\xcf\x11\xe0 fake biff", filename="plik.XLS"))

    def test_unknown_extension_is_read_as_csv(self):
        # Brak listy dozwolonych rozszerzeń: wszystko poza .xlsx/.xls idzie jako CSV.
        body = _csv(["lokalizacja", "data potw"], [["A1", "2026-08-01 08:00:00"]])
        self._check("txt_as_csv", self._post(body, filename="dane.txt", name="Txt"))

    def test_empty_xlsx(self):
        self._check("empty_xlsx", self._post(_xlsx(None, []), filename="e.xlsx", ctype=XLSX))

    def test_blank_lines_only_csv(self):
        # Same puste linie → csv daje [[]…] (lista niepusta) → brak kolumny lokalizacji.
        self._check("blank_csv", self._post(b"\n\n\n"))

    def test_missing_location_column(self):
        self._check("no_loc_col", self._post(_csv(["data potw", "picker"], [["x", "y"]])))

    def test_missing_date_column(self):
        self._check("no_date_col", self._post(_csv(["lokalizacja", "picker"], [["A1", "y"]])))

    def test_header_only(self):
        self._check("header_only", self._post(_csv(["lokalizacja", "data potw"], [])))

    def test_csv_cp1250_falls_back_from_utf8(self):
        # Naprawione: CSV spoza UTF-8 czytany jako cp1250 (eksport SAP z polskiego
        # Windowsa) — nagłówek „użytkownik” i wartość „Łukasz” zachowują polskie znaki.
        body = _csv(["lokalizacja", "data potw", "użytkownik"],
                    [["A1", "2026-08-01 08:00", "Łukasz"]], bom=False, enc="cp1250")
        self._check("csv_cp1250", self._post(body, name="CP1250"))

    # ── CSV: formaty daty/czasu, parsowanie pól ────────────────────────────
    def test_csv_combined_datetime_formats(self):
        header = ["Lokalizacja", "Data potwierdzenia", "Potwierdzone przez", "Działanie",
                  "Materiał", "Ilość potw.", "JM", "Partia"]
        long_loc = "L" * 60
        rows = [
            ["A-01", "2026-08-01 08:00:00", "Jan", "PICK", "M1", "2", "KAR", "L1"],
            ["A-02", "02.08.2026 09:15:30", "Ewa", "PUT", "M2", "1 234,5", "OP", "L2"],
            ["A-03", "03.08.2026 10:05", "  Olek  ", "", "", "abc", "", ""],
            ["A-04", "2026-08-04T11:00", "", "", "", "0", "", ""],
            ["A-05", "05/08/2026 12:30", "", "", "", "1.234,5", "", ""],  # „1.234,5” → 1234.5
            ["A-06", "2026-08-06 13:45", "", "", "", "", "", ""],
            # Nieparsowalna data → wiersz pomijany po cichu.
            ["A-07", "wczoraj", "X", "", "", "", "", ""],
            # Naprawione: sama data bez czasu → północ (wcześniej wiersz pomijany).
            ["A-08", "2026-08-08", "X", "", "", "", "", ""],
            # Pusta lokalizacja → pomijany.
            ["", "2026-08-09 08:00", "X", "", "", "", "", ""],
            ["  ", "2026-08-09 08:00", "X", "", "", "", "", ""],
            # Za krótki wiersz → brakujące komórki = None.
            ["A-10", "2026-08-10 08:00"],
            [long_loc, "2026-08-11 08:00", "P" * 120, "T" * 60, "M" * 60, "3", "U" * 30,
             "Z" * 40],
        ]
        self._check("csv_combined", self._post(_csv(header, rows), filename="Picks.CSV",
                                               name="CSV"))

    def test_csv_qty_with_nbsp(self):
        body = _csv(["lokalizacja", "data potw", "ilość"],
                    [["A1", "2026-08-01 08:00", "1\xa0234"]])
        self._check("csv_qty_nbsp", self._post(body, name="NBSP"))

    def test_csv_separate_date_and_time_columns(self):
        # Naprawione: sama data („2026-08-01”, „01.08.2026”) jest parsowana i łączona
        # z czasem z osobnej kolumny (wcześniej takie wiersze CSV były cicho odrzucane).
        # Gdy kolumna daty zawiera też czas, czas z osobnej kolumny go nadpisuje.
        header = ["Msc. skład.", "Data", "Czas potw.", "Użytkownik"]
        rows = [
            ["B0-01-100A", "2026-08-01", "08:15:00", "Jan"],
            ["B0-01-100B", "01.08.2026", "8:15", "Jan"],
            ["B0-01-100C", "2026-08-01 07:00", "09:30", "Jan"],      # czas z osobnej kolumny
            ["B0-01-100D", "2026-08-01 07:00", "9.30", "Jan"],       # czas nieparsowalny
            ["B0-01-100E", "2026-08-01 07:00", "", "Jan"],           # pusty czas → string ""
        ]
        self._check("csv_date_time_split", self._post(_csv(header, rows), name="Split"))

    def test_csv_time_column_same_as_datetime_column(self):
        # „Timestamp” łapie dt ("timestamp") i fallback czasu ("time") → ta sama kolumna,
        # więc czas nie jest traktowany jako osobny.
        # Naprawione: krótkie aliasy JM („me”, „jm”) dopasowujemy jako całe słowo, a
        # kolumna już przypisana nie jest brana ponownie — bez kolumny „JM” unit pusty
        # (wcześniej trafiał tam znacznik czasu).
        body = _csv(["Location", "Timestamp", "Operator"],
                    [["A1", "2026-08-01 08:00:00", "Ola"]])
        self._check("csv_timestamp_unit_quirk", self._post(body, name="TS"))

    def test_csv_date_fallback_column(self):
        # Brak kolumny „data potw/conf/...” → zwykła „Date”; brak kolumny czasu.
        body = _csv(["Storage Bin", "Date", "User", "Task type"],
                    [["C1", "01.08.2026 06:00", "u1", "3040"]])
        self._check("csv_date_fallback", self._post(body, name="Fallback"))

    def test_csv_without_bom_and_default_name(self):
        # Brak/pusta nazwa → nazwa = nazwa pliku.
        body = _csv(["lokalizacja", "data potw"], [["A1", "2026-08-01 08:00"]], bom=False)
        self._check("csv_default_name", self._post(body, filename="Plik.csv", name=None))

    def test_blank_name_uses_filename(self):
        body = _csv(["lokalizacja", "data potw"], [["A1", "2026-08-01 08:00"]])
        self._check("csv_blank_name", self._post(body, filename="spacje.csv", name="   "))

    def test_all_rows_skipped_creates_no_batch(self):
        # Naprawione: bez żadnego poprawnego wiersza partia NIE powstaje — błąd
        # „Nie znaleziono poprawnych wierszy…” i powrót na listę.
        body = _csv(["lokalizacja", "data potw"], [["", "2026-08-01 08:00"], ["A1", "x"]])
        self._check("all_skipped", self._post(body, name="Pusta"))

    # ── XLSX ──────────────────────────────────────────────────────────────
    def test_xlsx_typed_cells_and_split_date_time(self):
        header = ["Źr. msc. skład.", "Data potw.", "Czas potw.", "Potwierdzone przez",
                  "Typ zadania", "Materiał", "Ilość", "AJM", "Partia", None]
        rows = [
            # Data jako datetime + czas jako time → połączone (data z datetime, czas z time).
            ["B0-01-100A", dt.datetime(2026, 8, 1, 23, 59), dt.time(8, 15), "Jan", "PICK",
             1001, 2, "KAR", 55, "ignored"],
            # Data jako date + czas jako string.
            ["B0-01-100B", dt.date(2026, 8, 2), "10:20:30", "Ewa", None, "M2", 1.5, "OP",
             "L2", None],
            # Data jako string z czasem + czas nieparsowalny → fallback na pełną datę.
            # Naprawione: liczbowa ilość 0 z xlsx zapisuje się jako 0.0 (wcześniej None).
            ["B0-01-100C", "03.08.2026 11:00", "xx", None, None, None, 0, None, None, None],
            # Data jako datetime bez kolumny czasu (pusta komórka) → sam datetime.
            ["B0-01-100D", dt.datetime(2026, 8, 4, 12, 0), None, None, None, None, None,
             None, None, None],
            # Sama date bez czasu → północ.
            ["B0-01-100E", dt.date(2026, 8, 5), None, None, None, None, "7", None, None,
             None],
            # Liczba jako lokalizacja → str().
            [12345, dt.datetime(2026, 8, 6, 6, 0), None, None, None, None, None, None,
             None, None],
            # Brak daty → pomijany.
            ["B0-01-100F", None, dt.time(9, 0), None, None, None, None, None, None, None],
            # Pusty wiersz.
            [None] * 10,
        ]
        self._check("xlsx_typed", self._post(_xlsx(header, rows), filename="Eksport.XLSX",
                                             name="XLSX", ctype=XLSX))

    def test_xlsx_header_only(self):
        self._check("xlsx_header_only",
                    self._post(_xlsx(["Lokalizacja", "Data potw."], []), filename="h.xlsx",
                               ctype=XLSX))

    def test_xlsx_numeric_headers(self):
        # Nagłówki liczbowe → str().lower(); brak kolumny lokalizacji.
        self._check("xlsx_numeric_headers",
                    self._post(_xlsx([1, 2], [["A1", "x"]]), filename="n.xlsx", ctype=XLSX))

    # ── zapis w paczkach / błędy zapisu ────────────────────────────────────
    def _big_body(self, n):
        rows = [[f"L{i:05d}", f"2026-08-{1 + i % 28:02d} 08:00"] for i in range(n)]
        return _csv(["lokalizacja", "data potw"], rows)

    def test_bulk_chunking_over_500_rows(self):
        calls = []
        real = PickerActivity.objects.bulk_create

        def spy(objs, *a, **kw):
            calls.append(len(objs))
            return real(objs, *a, **kw)

        with mock.patch.object(PickerActivity.objects, "bulk_create", side_effect=spy):
            res = self._post(self._big_body(1201), name="Duża")
        batch = PickerActivityBatch.objects.get(name="Duża")
        self.assertEqual(calls, [500, 500, 201])
        self._check("bulk_chunking", {**res, "batch": {
            "row_count": batch.row_count, "date_from": batch.date_from,
            "date_to": batch.date_to}}, with_db=False)

    def test_bulk_error_in_chunk_deletes_batch(self):
        real = PickerActivity.objects.bulk_create
        state = {"n": 0}

        def flaky(objs, *a, **kw):
            state["n"] += 1
            if state["n"] == 2:
                raise ValueError("awaria DB")
            return real(objs, *a, **kw)

        with mock.patch.object(PickerActivity.objects, "bulk_create", side_effect=flaky):
            res = self._post(self._big_body(1100), name="Awaria")
        self._check("bulk_error_chunk", res)

    def test_bulk_error_in_final_flush_deletes_batch(self):
        with mock.patch.object(PickerActivity.objects, "bulk_create",
                               side_effect=ValueError("koniec")):
            res = self._post(self._big_body(3), name="Awaria2")
        self._check("bulk_error_final", res)

    def test_each_upload_creates_new_batch(self):
        body = _csv(["lokalizacja", "data potw"], [["A1", "2026-08-01 08:00"]])
        first = self._post(body, name="Powtórka")
        # Komunikaty pierwszego żądania nie zostały „skonsumowane” przez szablon, więc
        # drugie żądanie widzi oba sukcesy (artefakt klienta testowego, nie widoku).
        second = self._post(body, name="Powtórka")
        self._check("repeat_upload", {"first": first, "second": second})
