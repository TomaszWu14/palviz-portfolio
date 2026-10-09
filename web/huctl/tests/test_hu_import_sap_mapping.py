"""Import wsadu „Dane do kontroli HU" wg realnego eksportu SAP (20 kolumn): mapowanie
Dokument→dostawa, Typ magazynu (gating), Odbiorca materiałów→KUNNR, Nazwa odbiorcy→recipient,
Potwierdzone przez→picker, Autor NIE→picker; pusty Dokument liczony jako błąd wsadu."""
from django.test import TestCase

from ui.models import HandlingUnit
from huctl.views.hu import _import_hu_rows, _map_hu_columns

# Nagłówki 1:1 jak SAP (importer lowercase'uje je sam przy _read_table; tu podajemy już małymi).
HEADER = [
    "jednostka obsługi", "produkt", "krótki opis produktu", "partia dostawcy",
    "termin ważności", "ilość pjm", "podst. jedn. miary", "jednostka alternat.",
    "ilość ajm", "zapas_hu.miejsce składowania", "dokument",
    "nagłówki.odbiorca materiałów", "nagłówki.utworz. dnia", "nagłówki.autor",
    "nagłówki.klucz kraju/regionu", "nagłówki.status pobrania", "potwierdzone przez",
    "nagłówki.nazwa odbiorcy 1", "nagłówki.utworzono o godz.", "lokalizacje.typ magazynu",
]


def _row(pick, ref, dokument, kunnr, autor, potw, nazwa, typ):
    return [pick, ref, "opis", "193716114N", "2031-05-01", 2880, "SZT", "PAZ", 1,
            "05L.01", dokument, kunnr, "2026-08-14", autor, "BA", "Zakończone", potw,
            nazwa, "09:18:50", typ]


class HuImportSapMappingTests(TestCase):
    def test_columns_map_to_expected_fields(self):
        idx = _map_hu_columns(HEADER)
        self.assertEqual(idx["shipment"], 10)      # K Dokument
        self.assertEqual(idx["warehouse"], 19)     # T Typ magazynu
        self.assertEqual(idx["kunnr"], 11)         # L Odbiorca materiałów (KOD)
        self.assertEqual(idx["recipient"], 17)     # R Nazwa odbiorcy 1
        self.assertEqual(idx["picker"], 16)        # Q Potwierdzone przez
        # „Autor" (N=13) nie może zostać zmapowany na pickera.
        self.assertNotEqual(idx.get("picker"), 13)

    def test_import_creates_hu_with_delivery_type_picker(self):
        ok, info = _import_hu_rows(HEADER, [
            _row("11582503", "DM15300-F", "81828209", "11131116", "PTESTOWY",
                 "JKOWAL", "ADRIA DEMO", "92EX")])
        self.assertTrue(ok)
        hu = HandlingUnit.objects.get(code="11582503")
        self.assertEqual(hu.warehouse_type, "92EX")        # gating z kol. Typ magazynu
        self.assertEqual(hu.picker, "JKOWAL")               # Potwierdzone przez, nie Autor
        self.assertFalse(hu.shipment.is_stock)             # Dokument → realna dostawa
        self.assertEqual(hu.shipment.kunnr, "11131116")    # KUNNR z Odbiorca materiałów

    def test_empty_dokument_counted_as_error(self):
        ok, info = _import_hu_rows(HEADER, [
            _row("HU1", "R1", "81828209", "11131116", "A", "JKOWAL", "N", "92EX"),
            _row("HU2", "R2", "", "11131116", "A", "JKOWAL", "N", "92EX")])   # brak Dokumentu
        self.assertTrue(ok)
        self.assertEqual(info["with_delivery"], 1)
        self.assertEqual(info["no_delivery"], 1)
