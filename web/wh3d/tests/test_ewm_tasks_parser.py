"""Parser eksportu zadań magazynowych EWM (WT): aliasy nagłówków, liczby, daty, błędne
wiersze, mapowanie rodzaju procesu → rodzaj ruchu, strumieniowe czytanie CSV/XLSX."""
import os
import tempfile
from datetime import date, datetime, time, timezone as dt_tz

from django.test import SimpleTestCase

from wh3d.ewm_tasks import (
    Scan, map_columns, map_kind, missing_required, parse_number, parse_overrides, parse_row,
    parse_stamp,
)

PL_HEADERS = ["Zadanie magazynowe", "Rodzaj procesu magazynowego", "Źródłowe miejsce składowania",
              "Docelowe miejsce składowania", "Produkt", "Partia", "Ilość docelowa w AJM", "AJM",
              "HU źródłowa", "HU docelowa", "Dokument", "Utworzono dn.", "Utworzono o",
              "Potwierdzono dn.", "Potwierdzono o", "Użytkownik", "Zasób", "Kolejka"]


class HeaderAliasTests(SimpleTestCase):
    def test_polish_sap_headers_map_every_field(self):
        cols = map_columns(PL_HEADERS)
        self.assertEqual(cols["task_no"], 0)
        self.assertEqual(cols["process_type"], 1)
        self.assertEqual(cols["src_location"], 2)
        self.assertEqual(cols["dst_location"], 3)
        self.assertEqual(cols["qty"], 6)
        self.assertEqual(cols["unit"], 7)
        self.assertEqual((cols["src_hu"], cols["dst_hu"]), (8, 9))
        self.assertEqual((cols["created_date"], cols["created_time"]), (11, 12))
        self.assertEqual((cols["confirmed_date"], cols["confirmed_time"]), (13, 14))
        self.assertEqual((cols["user"], cols["resource"], cols["queue"]), (15, 16, 17))
        self.assertEqual(missing_required(cols), [])

    def test_english_and_technical_headers(self):
        cols = map_columns(["TANUM", "Warehouse Process Type", "Source Storage Bin", "NLPLA",
                            "Product", "Confirmed At", "Resource", "Confirmed By"])
        self.assertEqual([cols[f] for f in ("task_no", "process_type", "src_location", "dst_location",
                                            "material", "confirmed_time", "resource", "user")],
                         list(range(8)))
        self.assertEqual(missing_required(cols), [])     # sam znacznik potwierdzenia wystarcza

    def test_header_with_suffix_and_each_column_used_once(self):
        cols = map_columns(["Źródłowe miejsce składowania (nr)", "Docelowe miejsce składowania", "Data potwierdzenia"])
        self.assertEqual((cols["src_location"], cols["dst_location"], cols["confirmed_date"]), (0, 1, 2))
        self.assertEqual(len(set(cols.values())), len(cols))

    def test_missing_required_columns_named_in_polish(self):
        self.assertEqual(missing_required(map_columns(["Produkt", "Partia"])),
                         ["lokalizacja źródłowa lub docelowa", "data/znacznik potwierdzenia"])


class ValueParsingTests(SimpleTestCase):
    def test_numbers_polish_english_sap(self):
        cases = {"1,5": 1.5, "1.234,5": 1234.5, "1,234.5": 1234.5, "1 234,5": 1234.5,
                 "1\xa0234": 1234.0, "5-": -5.0, "2.000.000": 2000000.0, "12": 12.0, 7: 7.0, "": None}
        for raw, want in cases.items():
            self.assertEqual(parse_number(raw), want, raw)
        with self.assertRaises(ValueError):
            parse_number("abc")

    def test_separate_date_and_time_columns(self):
        dt = parse_stamp("02.03.2026", "06:15:30", "UTC")
        self.assertEqual(dt, datetime(2026, 3, 2, 6, 15, 30, tzinfo=dt_tz.utc))

    def test_excel_cells_date_midnight_plus_time(self):
        dt = parse_stamp(datetime(2026, 3, 2), time(7, 5), "UTC")
        self.assertEqual((dt.hour, dt.minute), (7, 5))
        dt = parse_stamp(date(2026, 3, 2), 0.5, "UTC")          # Excel: ułamek doby
        self.assertEqual(dt.hour, 12)

    def test_sap_timestamp_and_iso(self):
        self.assertEqual(parse_stamp("20260302061530", tz="UTC").minute, 15)
        self.assertEqual(parse_stamp("20260302061530,1234567", tz="UTC").second, 30)
        self.assertEqual(parse_stamp("2026-03-02T06:15:00", tz="UTC").hour, 6)

    def test_timestamp_only_in_time_column(self):
        self.assertEqual(parse_stamp(None, "2026-03-02 06:15:00", "UTC").day, 2)
        self.assertIsNone(parse_stamp(None, "06:15:00", "UTC"))

    def test_timezone_local_vs_utc(self):
        local = parse_stamp("02.07.2026", "12:00", "Europe/Warsaw")
        self.assertEqual(local.astimezone(dt_tz.utc).hour, 10)   # CEST = UTC+2

    def test_empty_and_bad_dates(self):
        self.assertIsNone(parse_stamp("", ""))
        self.assertIsNone(parse_stamp("00.00.0000"))
        with self.assertRaises(ValueError):
            parse_stamp("32.13.2026")


class RowTests(SimpleTestCase):
    cols = map_columns(PL_HEADERS)

    def _row(self, **over):
        base = dict(zip(PL_HEADERS, ["000100200300", "1010", "GR-ZONE", "B0-01-100A", "M1", "L1", "12,5",
                                     "KAR", "", "HU1", "180001", "02.03.2026", "06:00:00", "02.03.2026",
                                     "06:10:00", "JKOWAL", "WOZEK01", "ZP02/PUTAWAY"], strict=True))
        base.update(over)
        return [base[h] for h in PL_HEADERS]

    def test_valid_row(self):
        r = parse_row(self._row(), self.cols, "UTC")
        self.assertEqual((r["kind"], r["src_location"], r["dst_location"]), ("putaway", "GR-ZONE", "B0-01-100A"))
        self.assertEqual((r["qty"], r["unit"], r["resource"], r["user"]), (12.5, "KAR", "WOZEK01", "JKOWAL"))
        self.assertEqual(r["confirmed_at"], datetime(2026, 3, 2, 6, 10, tzinfo=dt_tz.utc))
        self.assertFalse(r["cancelled"])

    def test_blank_row_is_skipped_not_an_error(self):
        self.assertIsNone(parse_row(["", None, "  "], self.cols))

    def test_bad_rows_raise_with_polish_reason(self):
        for over, msg in (({"Źródłowe miejsce składowania": "", "Docelowe miejsce składowania": ""}, "lokalizacji"),
                          ({"Ilość docelowa w AJM": "dużo"}, "ilość"),
                          ({"Potwierdzono dn.": "31.02.2026"}, "data")):
            with self.assertRaisesRegex(ValueError, msg):
                parse_row(self._row(**over), self.cols)

    def test_excel_number_codes_become_text(self):
        r = parse_row(self._row(**{"Zadanie magazynowe": 100200300.0, "Produkt": 4711.0}), self.cols)
        self.assertEqual((r["task_no"], r["material"]), ("100200300", "4711"))


class KindMappingTests(SimpleTestCase):
    def test_default_mapping(self):
        cases = [(("1010",), "putaway"), (("2010", "ZP02/EXPOPICK1"), "picking"),
                 (("2010", "ZP02/STAGING"), "outbound"), (("21GL",), "outbound"),
                 (("3010",), "replenishment"), (("301D",), "replenishment"), (("3030",), "move"),
                 (("4010",), "move"), (("9010",), "putaway"), (("9999",), "move"),
                 (("X020",), "outbound"), (("", "", "PICK"), "picking"), (("", "", "", "1"), "putaway"),
                 (("", "", "", "2"), "outbound"), (("",), "move")]
        for args, want in cases:
            self.assertEqual(map_kind(*args), want, args)

    def test_overrides_win(self):
        overrides, errors = parse_overrides("2010 = kompletacja\n3040=przesunięcie\n# komentarz\n21gl = Wydanie")
        self.assertEqual(overrides, {"2010": "picking", "3040": "move", "21GL": "outbound"})
        self.assertEqual(errors, [])
        self.assertEqual(map_kind("2010", "", overrides=overrides), "picking")

    def test_bad_override_lines_reported(self):
        overrides, errors = parse_overrides("2010\n3010 = coś\n= wydanie")
        self.assertEqual(overrides, {})
        self.assertEqual(len(errors), 3)


class ScanFileTests(SimpleTestCase):
    def _file(self, suffix, data):
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        self.addCleanup(os.remove, path)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_cp1250_semicolon_csv_with_title_line(self):
        text = ("Lista zadań magazynowych\n"
                "Zadanie magazynowe;Rodzaj procesu magazynowego;Źródłowe miejsce składowania;"
                "Docelowe miejsce składowania;Ilość;Data potwierdzenia;Czas potwierdzenia;Zasób;Status\n"
                "1;1010;GR-ZONE;B0-01-100A;1,5;02.03.2026;06:10:00;WÓZEK1;C\n"
                "2;2010;B0-01-200A;GI-ZONE;2;02.03.2026;06:05:00;WÓZEK2;C\n"
                "3;2010;B0-01-200A;GI-ZONE;x;02.03.2026;06:06:00;WÓZEK2;C\n"
                ";;;;;;;;\n"
                "4;3030;B0-01-100A;B0-02-100A;1;;;;C\n"
                "5;3030;B0-01-100A;B0-02-100A;1;02.03.2026;06:20:00;;A\n")
        scan = Scan(self._file(".csv", text.encode("cp1250")), "wt.csv", "UTC")
        rows = list(scan)
        scan.close()
        self.assertEqual(scan.missing, [])
        self.assertEqual([r["task_no"] for r in rows], ["1", "2", "4"])
        self.assertEqual(rows[0]["resource"], "WÓZEK1")              # polskie znaki z cp1250
        self.assertEqual((scan.rows, scan.imported, scan.error_count), (5, 3, 1))
        self.assertEqual(scan.errors, [{"line": 5, "error": "nieczytelna ilość „x”"}])
        self.assertEqual((scan.unconfirmed, scan.cancelled), (1, 1))
        self.assertEqual((scan.first.minute, scan.last.minute), (5, 10))
        stats = scan.stats()
        self.assertEqual({k["kind"]: k["count"] for k in stats["kinds"]}["outbound"], 1)
        self.assertEqual(stats["resources"], 2)

    def test_xlsx_with_excel_date_and_time_cells(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Zadanie magazynowe", "Docelowe miejsce składowania", "Potwierdzono dn.", "Potwierdzono o",
                   "Rodzaj procesu magazynowego"])
        ws.append([100200300, "B0-01-100A", datetime(2026, 3, 2), time(8, 30), 1010])
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        self.addCleanup(os.remove, path)
        wb.save(path)
        scan = Scan(path, "wt.xlsx", "UTC")
        rows = list(scan)
        scan.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_no"], "100200300")
        self.assertEqual(rows[0]["kind"], "putaway")
        self.assertEqual(rows[0]["confirmed_at"], datetime(2026, 3, 2, 8, 30, tzinfo=dt_tz.utc))

    def test_missing_columns_yield_nothing(self):
        scan = Scan(self._file(".csv", b"Produkt;Partia\nM1;L1\n"), "x.csv")
        self.assertEqual(list(scan), [])
        scan.close()
        self.assertTrue(scan.missing)

    def test_xls_rejected_with_hint(self):
        with self.assertRaisesRegex(ValueError, "xlsx"):
            Scan(self._file(".xls", b"x"), "stary.xls")
