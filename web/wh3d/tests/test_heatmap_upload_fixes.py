"""Testy regresji naprawionych błędów importu aktywności do heatmapy (``heatmap_upload``).

Każdy test odpowiada jednemu błędowi przypiętemu wcześniej w testach charakteryzacyjnych
(komentarze „BŁĄD” w ``test_heatmap_upload_characterization``).
"""
import csv
import datetime as dt
import io

import openpyxl
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.timezone import localtime

from ui.roles import GROUP_MASTER_DATA
from wh3d.models import PickerActivity, PickerActivityBatch
from wh3d.views import heatmap_upload_parse as p

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _csv(headers, rows, enc="utf-8"):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(headers)
    w.writerows(rows)
    return buf.getvalue().encode(enc)


def _xlsx(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class ParseHelpersTests(SimpleTestCase):
    # Błąd 1: sama data (bez czasu) w CSV.
    def test_date_only_string_combined_with_time_column(self):
        for raw in ("2026-08-01", "01.08.2026", "01/08/2026"):
            with self.subTest(raw=raw):
                got = localtime(p.parse_when(raw, "08:15"))
                self.assertEqual((got.date(), got.time()), (dt.date(2026, 8, 1), dt.time(8, 15)))

    def test_date_only_string_without_time_is_midnight(self):
        got = localtime(p.parse_when("01.08.2026", None))
        self.assertEqual((got.date(), got.time()), (dt.date(2026, 8, 1), dt.time(0, 0)))

    def test_date_only_with_unparseable_time_falls_back_to_midnight(self):
        got = localtime(p.parse_when("2026-08-01", "9.30"))
        self.assertEqual(got.time(), dt.time(0, 0))

    # Błąd 2: krótkie aliasy JM dopasowane jako podciąg.
    def test_unit_short_alias_is_not_a_substring_match(self):
        cols = p.detect_columns(["location", "timestamp", "name", "time"])
        self.assertIsNone(cols["unit"])

    def test_unit_short_alias_matches_whole_word(self):
        for header in ("jm", "me", "ajm", "[me]"):
            with self.subTest(header=header):
                cols = p.detect_columns(["lokalizacja", "data potw", header])
                self.assertEqual(cols["unit"], 2)

    def test_assigned_column_is_not_reused(self):
        # „ilość jm” to qty — nie może zostać drugi raz wzięta jako JM.
        cols = p.detect_columns(["lokalizacja", "data potw", "ilość jm"])
        self.assertEqual(cols["qty"], 2)
        self.assertIsNone(cols["unit"])

    # Błąd 3 i 4: ilość 0 z xlsx oraz format europejski „1.234,5”.
    def test_parse_qty(self):
        cases = {0: 0.0, 2.5: 2.5, 2: 2.0, "0": 0.0, "1.234,5": 1234.5, "1 234,5": 1234.5,
                 "1\xa0234": 1234.0, "1,234.5": 1234.5, "2,5": 2.5, "1.5": 1.5, "": None,
                 None: None, "abc": None}
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(p.parse_qty(raw), want)

    # Błąd 6: CSV w cp1250.
    def test_read_table_cp1250(self):
        f = SimpleUploadedFile("a.csv", "lokalizacja,użytkownik\nA1,Łukasz\n".encode("cp1250"))
        headers, rows = p.read_table(f)
        self.assertEqual(headers, ["lokalizacja", "użytkownik"])
        self.assertEqual(rows, [["A1", "Łukasz"]])


class HeatmapUploadFixViewTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="hm-fix", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    def _post(self, content, filename="p.csv", name="H", ctype="text/csv"):
        resp = self.client.post(reverse("ui:heatmap_upload"), {
            "name": name, "file": SimpleUploadedFile(filename, content, content_type=ctype)})
        self.assertEqual(resp.status_code, 302)
        return resp, [(m.level_tag, m.message) for m in get_messages(resp.wsgi_request)]

    def test_csv_separate_date_column_rows_are_imported(self):
        body = _csv(["Msc. skład.", "Data", "Czas potw."],
                    [["B0-01-100A", "2026-08-01", "08:15:00"],
                     ["B0-01-100B", "01.08.2026", "8:15"]])
        self._post(body)
        self.assertEqual(PickerActivity.objects.count(), 2)

    def test_timestamp_not_saved_as_unit(self):
        self._post(_csv(["Location", "Timestamp", "Operator"],
                        [["A1", "2026-08-01 08:00:00", "Ola"]]))
        self.assertEqual(PickerActivity.objects.get().unit, "")

    def test_xlsx_zero_qty_is_zero(self):
        self._post(_xlsx(["Lokalizacja", "Data potw.", "Ilość"],
                         [["A1", dt.datetime(2026, 8, 1, 8, 0), 0]]),
                   filename="x.xlsx", ctype=XLSX)
        self.assertEqual(PickerActivity.objects.get().qty, 0.0)

    def test_csv_european_thousands_qty(self):
        self._post(_csv(["lokalizacja", "data potw", "ilość"],
                        [["A1", "2026-08-01 08:00", "1.234,5"]]))
        self.assertEqual(PickerActivity.objects.get().qty, 1234.5)

    def test_no_valid_rows_creates_no_batch(self):
        resp, msgs = self._post(_csv(["lokalizacja", "data potw"],
                                     [["", "2026-08-01 08:00"], ["A1", "x"]]))
        self.assertFalse(PickerActivityBatch.objects.exists())
        self.assertEqual(resp["Location"], reverse("ui:heatmap_list"))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0][0], "error")
        self.assertTrue(msgs[0][1].startswith("Nie znaleziono poprawnych wierszy"))

    def test_cp1250_csv_keeps_polish_header_and_values(self):
        self._post(_csv(["lokalizacja", "data potw", "użytkownik"],
                        [["A1", "2026-08-01 08:00", "Łukasz"]], enc="cp1250"))
        self.assertEqual(PickerActivity.objects.get().picker_name, "Łukasz")

    def test_long_batch_name_is_truncated(self):
        limit = PickerActivityBatch._meta.get_field("name").max_length
        self._post(_csv(["lokalizacja", "data potw"], [["A1", "2026-08-01 08:00"]]),
                   name="N" * (limit + 50))
        self.assertEqual(PickerActivityBatch.objects.get().name, "N" * limit)
