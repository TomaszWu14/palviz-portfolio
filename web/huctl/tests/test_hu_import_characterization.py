"""Testy charakteryzacyjne ``huctl.hu_import.import_hu_rows`` (CODE-001).

Przypinają AKTUALNE zachowanie wspólnego rdzenia importu HU (upload pliku + pull
Power BI): wynik funkcji oraz stan bazy (przesyłki, HU, pozycje) — strażnik przed
refaktorem (rozbicie funkcji CC=54). Oczekiwane wartości leżą w
``data/hu_import_snapshot.json``; PK są pomijane (sekwencje PostgreSQL w CI nie
resetują się między testami) — HU wiążemy z przesyłką po nazwie, pozycje z HU po kodzie.
Regeneracja (TYLKO świadomie, gdy zmiana zachowania jest zamierzona):
``HU_IMPORT_UPDATE_SNAPSHOT=1 python manage.py test huctl.tests.test_hu_import_characterization``
"""
import json
import os
from pathlib import Path
from unittest import mock

from django.test import TestCase

from huctl.hu_import import import_hu_rows
from huctl.models import HandlingUnit, HandlingUnitItem
from huctl.models_control import HUControlAttempt
from ui.models import Customer, PalletizationInstruction, Product, Shipment

SNAPSHOT_FILE = Path(__file__).parent / "data" / "hu_import_snapshot.json"
_UPDATE = os.environ.get("HU_IMPORT_UPDATE_SNAPSHOT") == "1"
_collected = {}

# Pełny nagłówek „wszystko naraz" (lowercase — tak jak podaje _read_table). Kolejność
# celowo miesza aliasy, by przypiąć priorytety dopasowania (typ dokumentu ≠ dostawa,
# partia producenta ≠ partia, data utworzenia ≠ termin, skompletowana ≠ picker).
FULL_HDR = ["typ dokumentu", "dokument", "jednostka obsługi", "kod klienta", "materiał",
            "opis", "partia producenta", "partia", "data utworzenia", "data dostawy",
            "termin ważności", "ilość", "jm", "miejsce składowania", "typ magazynu",
            "nazwa odbiorcy 1", "nr wz", "kraj", "skompletowana", "status zapasu",
            "picking_done", "potwierdzone przez", "waga", "długość", "szerokość", "wysokość"]


def _row(**over):
    base = {"typ dokumentu": "LF", "dokument": "D-1", "jednostka obsługi": "HU-A",
            "kod klienta": "", "materiał": "P1", "opis": "", "partia producenta": "VB1",
            "partia": "L1", "data utworzenia": "", "data dostawy": "",
            "termin ważności": "2027-05-31", "ilość": "10", "jm": "OP",
            "miejsce składowania": "", "typ magazynu": "", "nazwa odbiorcy 1": "",
            "nr wz": "", "kraj": "", "skompletowana": "", "status zapasu": "",
            "picking_done": "", "potwierdzone przez": "", "waga": "", "długość": "",
            "szerokość": "", "wysokość": ""}
    unknown = set(over) - set(base)
    assert not unknown, unknown
    base.update(over)
    return [base[h] for h in FULL_HDR]


def _norm(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


_SH_FIELDS = ("name", "is_stock", "wz_number", "kunnr", "destination_country",
              "customer__name", "outbound_delivery_date", "outbound_created_date",
              "picking_complete")
_HU_FIELDS = ("code", "shipment__name", "shipment__is_stock", "seq", "status", "location",
              "warehouse_type", "recipient_type", "picker", "is_completed", "stock_status",
              "length_cm", "width_cm", "height_cm", "weight_kg")
_IT_FIELDS = ("ref_code", "product__code", "description", "lot", "expiry", "vendor_batch",
              "alt_unit", "alt_qty", "base_unit", "base_qty", "expected_qty", "unit",
              "weight_kg")


def _db_state():
    """Stan bazy bez PK; znaczniki czasu tylko jako „ustawiony / nie"."""
    ships = []
    for sh in Shipment.objects.order_by("name", "is_stock", "created_at", "id"):
        d = {f: getattr(sh, f) for f in _SH_FIELDS if "__" not in f}
        d["customer__name"] = sh.customer.name if sh.customer_id else None
        d["picking_complete_at_set"] = sh.picking_complete_at is not None
        ships.append(d)
    hus = []
    for hu in HandlingUnit.objects.order_by("code", "id").select_related("shipment"):
        d = {f: getattr(hu, f) for f in _HU_FIELDS if "__" not in f}
        d["shipment__name"] = hu.shipment.name
        d["shipment__is_stock"] = hu.shipment.is_stock
        d["last_seen_at_set"] = hu.last_seen_at is not None
        hus.append(d)
    items = [dict(zip(("hu__code",) + _IT_FIELDS, r, strict=True)) for r in
             HandlingUnitItem.objects.order_by("hu__code", "id")
             .values_list("hu__code", *_IT_FIELDS)]
    return _norm({"shipments": ships, "hus": hus, "items": items})


class HuImportRowsCharacterizationTests(TestCase):
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
        p1 = Product.objects.create(code="P1", name="Produkt P1")
        Product.objects.create(code="BP-30F", name="Opaska")
        Product.objects.create(code="OLD", name="Nieaktywny", is_active=False)
        PalletizationInstruction.objects.create(
            product=p1, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=4, demand_pcs=100, is_active=True)
        # Starsza wersja z innym OP/KAR — wygrywa najnowsza (order_by -version).
        PalletizationInstruction.objects.create(
            product=p1, version=0, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=7, demand_pcs=100, is_active=True)
        Customer.objects.create(name="Klient 123", kunnr="0000123")

    # ── narzędzia ──────────────────────────────────────────────────────────
    def _run(self, header, rows):
        ok, info = import_hu_rows(header, rows)
        return {"ok": ok, "info": _norm(info)}

    def _check(self, name, result):
        got = dict(result, db=_db_state())
        if _UPDATE:
            _collected[name] = got
            return
        expected = json.loads(SNAPSHOT_FILE.read_text("utf-8"))[name]
        self.assertEqual(got, expected)

    # ── walidacja wejścia ──────────────────────────────────────────────────
    def test_missing_required_columns(self):
        self._check("missing_columns", self._run(["dokument", "opis", "ilość"], [["D", "x", "1"]]))

    def test_missing_ref_column_only(self):
        self._check("missing_ref", self._run(["jednostka obsługi", "ilość"], [["HU", "1"]]))

    def test_too_many_rows(self):
        with mock.patch("huctl.hu_import.MAX_IMPORT_ROWS", 2):
            res = self._run(FULL_HDR, [_row(), _row(), _row()])
        self._check("too_many_rows", res)

    def test_rows_at_limit_pass(self):
        with mock.patch("huctl.hu_import.MAX_IMPORT_ROWS", 2):
            res = self._run(FULL_HDR, [_row(), _row(**{"jednostka obsługi": "HU-B"})])
        self._check("rows_at_limit", res)

    # ── pełny feed: aliasy, daty, liczby, produkty ─────────────────────────
    def test_full_feed(self):
        rows = [
            # 1: pełny wiersz, kunnr z zerami wiodącymi → klient; ppc=4 → KAR.
            _row(**{"kod klienta": "123", "data utworzenia": "2026-08-01",
                    "data dostawy": "05.08.2026", "nr wz": "WZ/2026/1" + "x" * 50,
                    "kraj": "deu", "skompletowana": "X", "status zapasu": "B6-DŁUGI-STATUS",
                    "picking_done": "tak", "potwierdzone przez": "jan",
                    "miejsce składowania": "A-01", "typ magazynu": "WT01",
                    "nazwa odbiorcy 1": "Szpital", "waga": "12,5", "długość": "120",
                    "szerokość": "80,0", "wysokość": "1 45", "ilość": "1 000,5"}),
            # 2: ta sama HU, REF złożony → BP-30F bez instrukcji (ppc 0) → alt = base.
            _row(**{"materiał": "(48301)V(BP-30F)", "termin ważności": "31/12/2027",
                    "partia producenta": "", "ilość": "abc", "jm": ""}),
            # 3: nieznany REF, opis z pliku, daty w innych formatach.
            _row(**{"materiał": "ZZZ-UNKNOWN", "opis": "Opis z pliku " + "y" * 150,
                    "termin ważności": "20271231", "partia": "L" * 30,
                    "partia producenta": "V" * 60, "ilość": "3"}),
            # 4: nieaktywny produkt — nie jest dopasowany.
            _row(**{"materiał": "OLD", "termin ważności": "12/31/2027", "ilość": "2"}),
            # 5: niepoprawna data + data z czasem (xlsx → str).
            _row(**{"jednostka obsługi": "HU-B", "termin ważności": "2027-13-45",
                    "data dostawy": "2026-09-01 00:00:00", "ilość": "8"}),
            _row(**{"jednostka obsługi": "HU-B", "termin ważności": "2027-06-30 12:00:00",
                    "ilość": "4"}),
            # 6: wiersze pomijane (brak pickHU / REF) — nie liczą się do statystyk.
            _row(**{"jednostka obsługi": "", "ilość": "99"}),
            _row(**{"materiał": "", "ilość": "99"}),
            # 7: stock (bez dokumentu) — kontener is_stock.
            _row(**{"dokument": "", "jednostka obsługi": "HU-S", "ilość": "5",
                    "picking_done": "nie"}),
            # 8: krótki wiersz (brak końcowych kolumn) i None w komórce.
            ["LF", "D-2", "HU-C", None, "P1"],
        ]
        self._check("full_feed", self._run(FULL_HDR, rows))

    def test_minimal_old_feed_headers(self):
        hdr = ["stock_oraz_dlt[jednostka obsługi]", "stock_oraz_dlt[produkt]",
               "stock_oraz_dlt[podst. jedn. miary]", "[sumdostępna_ilość]"]
        self._check("old_feed", self._run(hdr, [["HU9", "(48301)V(BP-30F)", "Sztuka", "10"],
                                                ["HU9", "P1", "", "8"]]))

    # ── ponowny import: przepinanie, podmiana pozycji, blokady ─────────────
    def test_reimport_moves_hu_and_replaces_items(self):
        import_hu_rows(FULL_HDR, [_row(), _row(**{"jednostka obsługi": "HU-B"})])
        # HU-A przeksięgowana na D-2; HU-B bez zmian dostawy, ale nowe pozycje;
        # metadane fill-if-blank vs. snapshotowe (completed / status zapasu / picking).
        sh1 = Shipment.objects.get(name="D-1")
        HandlingUnit.objects.filter(code="HU-B").update(location="STARA", is_completed=True)
        sh1.wz_number = "WZ-STARY"
        sh1.picking_complete = True
        sh1.save()
        res = self._run(FULL_HDR, [
            _row(**{"dokument": "D-2", "ilość": "1", "nr wz": "WZ-NOWY"}),
            _row(**{"jednostka obsługi": "HU-B", "ilość": "2", "miejsce składowania": "NOWA",
                    "skompletowana": "nie", "nr wz": "WZ-NOWY", "picking_done": "nie",
                    "status zapasu": "F2"}),
        ])
        self._check("reimport_move", res)

    def test_seq_continues_from_max_with_gap(self):
        sh = Shipment.objects.create(name="D-1")
        HandlingUnit.objects.create(shipment=sh, seq=1, code="OLD-1")
        HandlingUnit.objects.create(shipment=sh, seq=5, code="OLD-5")
        other = Shipment.objects.create(name="D-9")
        HandlingUnit.objects.create(shipment=other, seq=1, code="HU-M")
        res = self._run(FULL_HDR, [_row(**{"jednostka obsługi": "HU-N"}),
                                   _row(**{"jednostka obsługi": "HU-M"})])
        self._check("seq_gap", res)

    def test_locked_hus_keep_items(self):
        """BIZ-001: HU nie-planned lub z próbami kontroli — pozycje zamrożone."""
        import_hu_rows(FULL_HDR, [_row(), _row(**{"jednostka obsługi": "HU-B"}),
                                  _row(**{"jednostka obsługi": "HU-C"})])
        HandlingUnit.objects.filter(code="HU-A").update(status="in_progress")
        hub = HandlingUnit.objects.get(code="HU-B")
        HUControlAttempt.objects.create(hu=hub)
        # Naprawione (fix-hu-import): BIZ-001 obejmuje całą HU — zablokowana HU nie dostaje
        # z feedu metadanych (lokalizacja, kompletacja, status zapasu) ani przepięcia na
        # inną dostawę (D-7 nie powstaje); info liczy też same HU (locked_hus).
        res = self._run(FULL_HDR, [
            _row(**{"dokument": "D-7", "ilość": "77", "miejsce składowania": "X-1",
                    "skompletowana": "X", "status zapasu": "Q4"}),
            _row(**{"jednostka obsługi": "HU-B", "ilość": "66"}),
            _row(**{"jednostka obsługi": "HU-C", "ilość": "55"}),
        ])
        self._check("locked", res)

    def test_hu_with_all_rows_skipped_keeps_items(self):
        import_hu_rows(FULL_HDR, [_row()])
        res = self._run(FULL_HDR, [_row(**{"materiał": ""}),
                                   _row(**{"jednostka obsługi": "HU-B"})])
        self._check("all_rows_skipped", res)

    # ── przesyłki: stock vs transport, duplikaty nazw, klient ──────────────
    def test_stock_and_transport_same_name_are_separate(self):
        Shipment.objects.create(name="Stock magazynowy", is_stock=False)
        res = self._run(FULL_HDR, [_row(**{"dokument": "", "jednostka obsługi": "HU-S"})])
        self._check("stock_vs_transport", res)

    def test_stock_and_same_named_delivery_in_one_file(self):
        # Naprawione (fix-hu-import): cache przesyłek kluczowany (nazwa, is_stock) — dostawa
        # o nazwie „Stock magazynowy" i wiersz stockowy w jednym pliku to DWIE przesyłki.
        res = self._run(FULL_HDR, [
            _row(**{"dokument": "Stock magazynowy", "jednostka obsługi": "HU-T"}),
            _row(**{"dokument": "", "jednostka obsługi": "HU-S"}),
        ])
        self._check("stock_name_collision", res)

    def test_duplicate_shipment_names_take_first(self):
        Shipment.objects.create(name="D-1", wz_number="PIERWSZA")
        Shipment.objects.create(name="D-1", wz_number="DRUGA")
        self._check("dup_names", self._run(FULL_HDR, [_row(**{"nr wz": "NOWY"})]))

    def test_customer_kept_when_kunnr_already_set(self):
        Shipment.objects.create(name="D-1", kunnr="555")
        other = Customer.objects.create(name="Klient 999", kunnr="999")
        Shipment.objects.create(name="D-2", customer=other)
        self._check("kunnr_existing", self._run(FULL_HDR, [
            _row(**{"kod klienta": "123"}),
            _row(**{"dokument": "D-2", "jednostka obsługi": "HU-B", "kod klienta": "000123"}),
            _row(**{"dokument": "D-3", "jednostka obsługi": "HU-C", "kod klienta": "000"}),
        ]))

    def test_picking_done_and_dates_fill_if_blank(self):
        from datetime import date
        Shipment.objects.create(name="D-1", outbound_delivery_date=date(2020, 1, 1),
                                destination_country="PL")
        self._check("fill_if_blank", self._run(FULL_HDR, [
            _row(**{"data dostawy": "2026-08-05", "data utworzenia": "01/08/2026",
                    "kraj": "de", "picking_done": "Y"}),
            _row(**{"picking_done": "0", "skompletowana": "C"}),
        ]))

    def test_dimensions_zero_and_invalid_not_filled(self):
        self._check("dims", self._run(FULL_HDR, [
            _row(**{"długość": "0", "szerokość": "abc", "wysokość": ""}),
            _row(**{"długość": "100", "szerokość": "60", "wysokość": "0,0"}),
        ]))
