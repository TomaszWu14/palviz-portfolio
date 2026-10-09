"""Testy charakteryzacyjne widoku ``planner_master_data_import`` (CODE-001).

Przypinają AKTUALNE zachowanie importu master daty (format SAP MARM i format
kolumn łączonych ``*_wymiar``): stan bazy (produkty, instrukcje „Import migracji”,
opakowania zbiorcze ``-opz``), komunikaty i przekierowanie — strażnik przed
refaktorem (rozbicie funkcji CC=78). Oczekiwane wartości leżą w
``data/master_data_import_snapshot.json``; PK są normalizowane (sekwencje
PostgreSQL w CI nie resetują się między testami), relacje opisujemy kodami/nazwami.
Regeneracja (TYLKO świadomie, gdy zmiana zachowania jest zamierzona):
``MD_IMPORT_UPDATE_SNAPSHOT=1 python manage.py test ui.tests.test_master_import_characterization``
"""
import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import InnerPack, PalletizationInstruction, Product
from ui.roles import GROUP_MASTER_DATA

SNAPSHOT_FILE = Path(__file__).parent / "data" / "master_data_import_snapshot.json"
_UPDATE = os.environ.get("MD_IMPORT_UPDATE_SNAPSHOT") == "1"
_collected = {}

URL_NAME = "ui:planner_master_data_import"

# Format B — kolumny łączone (duplikowane nagłówki „Ilość…” czytane pozycyjnie: *_wymiar + 2).
B_HEADER = (
    "ref_code;opis_pl;opis_en;Podstawowa jednostka miary;rodzina;Producent;TXT_SHORT_PL;"
    "sztuka_wymiar;sztuka_ean;Ilość podstawowej jednostki miary;sztuka_artwork_ref;"
    "op_wymiar;op_ean;Ilość podstawowej jednostki miary;op_artwork_ref;"
    "opz_wymiar;opz_ean;Ilość podstawowej jednostki miary;opz_artwork_ref;"
    "karton_wymiar;karton_ean;Ilość podstawowej jednostki miary;karton_artwork_ref"
)


def _b(code, opis="", short="", szt="", kar="", kar_qty="", sup="", ean="",
       op="", op_qty="", opz="", opz_qty=""):
    cols = [""] * 23
    cols[0], cols[1], cols[5], cols[6] = code, opis, sup, short
    cols[7], cols[8] = szt, ean
    cols[11], cols[13] = op, op_qty
    cols[15], cols[17] = opz, opz_qty
    cols[19], cols[21] = kar, kar_qty
    return ";".join(cols)


# Format A — SAP MARM (wiersz na jednostkę miary, grupowane po materiale).
A_HEADER = ("Materiał;Alternatywna jednostka miary;Mianownik;Licznik;"
            "Długość;Szerokość;Wysokość;Waga brutto;Kod EAN/UPC")


def _a(mat, ajm, mian="1", licz="1", l="", w="", h="", brutto="", ean=""):
    return ";".join(str(x) for x in [mat, ajm, mian, licz, l, w, h, brutto, ean])


def _norm(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _db_state():
    """Stan bazy bez PK — relacje wyrażone kodem produktu / nazwą opakowania."""
    products = [
        {"code": p.code, "name": p.name, "ean": p.ean, "supplier_short": p.supplier_short,
         "unit": [p.unit_length_cm, p.unit_width_cm, p.unit_height_cm],
         "is_active": p.is_active}
        for p in Product.objects.order_by("code")]
    instrs = [
        {"product": i.product.code, "version": i.version, "name": i.name,
         "pallet_code": i.pallet_code, "pallet": [i.pallet_length_cm, i.pallet_width_cm],
         "max_height_total_cm": i.max_height_total_cm,
         "pallet_base_height_cm": i.pallet_base_height_cm, "max_weight_kg": i.max_weight_kg,
         "carton": [i.carton_l, i.carton_w, i.carton_h], "unit_weight": i.unit_weight,
         "pcs_per_carton": i.pcs_per_carton, "carton_tare": i.carton_tare,
         "demand_pcs": i.demand_pcs, "is_active": i.is_active,
         "units_per_piece": i.units_per_piece,
         "inner_pack": i.inner_pack.name if i.inner_pack_id else None,
         "pcs_per_inner_pack": i.pcs_per_inner_pack, "packs_per_carton": i.packs_per_carton,
         "layouts_empty": not i.layouts}
        for i in PalletizationInstruction.objects.select_related("product", "inner_pack")
        .order_by("product__code", "version", "name")]
    packs = [
        {"name": ip.name, "dims": [ip.length_cm, ip.width_cm, ip.height_cm],
         "units_per_pack": ip.units_per_pack,
         "sales_unit": [ip.sales_unit_l_cm, ip.sales_unit_w_cm, ip.sales_unit_h_cm],
         "sales_units_per_pack": ip.sales_units_per_pack, "is_active": ip.is_active}
        for ip in InnerPack.objects.order_by("name")]
    return _norm({"products": products, "instructions": instrs, "inner_packs": packs})


class MasterDataImportCharacterizationTests(TestCase):
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
        u = get_user_model().objects.create_user(username="md-char", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    # ── narzędzia ──────────────────────────────────────────────────────────
    def _post(self, content=None, name="import.csv", **extra):
        data = dict(extra)
        if content is not None:
            if isinstance(content, str):
                content = content.encode("utf-8")
            data["file"] = SimpleUploadedFile(name, content, content_type="text/csv")
        resp = self.client.post(reverse(URL_NAME), data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("ui:planner_products"))
        msgs = [[m.level_tag, m.message] for m in get_messages(resp.wsgi_request)]
        return msgs

    def _check(self, name, msgs):
        got = {"messages": msgs, "db": _db_state()}
        if _UPDATE:
            _collected[name] = got
            return
        expected = json.loads(SNAPSHOT_FILE.read_text("utf-8"))[name]
        self.assertEqual(got, expected)

    # ── walidacja wejścia ──────────────────────────────────────────────────
    def test_no_file(self):
        self._check("no_file", self._post())

    def test_file_too_large(self):
        self._check("too_large", self._post(b"x" * (20 * 1024 * 1024 + 1)))

    def test_file_at_size_limit_is_processed(self):
        # Dokładnie 20 MB przechodzi bramkę rozmiaru (warunek „>”), dalej czysty nagłówek.
        body = (B_HEADER + "\n").encode("utf-8")
        body += b"\n" * (20 * 1024 * 1024 - len(body))
        self._check("at_size_limit", self._post(body))

    def test_empty_file(self):
        self._check("empty_file", self._post(b""))

    def test_header_only(self):
        self._check("header_only", self._post(B_HEADER + "\n"))

    def test_broken_xlsx_reports_error(self):
        msgs = self._post(b"not-a-zip", name="plik.xlsx")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0][0], "error")
        self.assertTrue(msgs[0][1].startswith("Błąd importu: "))
        self._check("broken_xlsx_db", [])

    def test_guard_and_method(self):
        url = reverse(URL_NAME)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)          # anon → logowanie
        self.client.force_login(get_user_model().objects.create_user(username="nobody"))
        self.assertEqual(self.client.post(url).status_code, 403)          # bez roli
        self.assertFalse(Product.objects.exists())

    # ── format B (kolumny łączone) ─────────────────────────────────────────
    def test_format_b_rich(self):
        # Istniejący produkt (nieaktywny) + ręczna instrukcja v1 + stara „Import migracji” v2.
        prod = Product.objects.create(code="B-UPD", name="stara", ean="111",
                                      supplier_short="OLD", is_active=False)
        PalletizationInstruction.objects.create(
            product=prod, version=1, name="Ręczna", carton_l=10, carton_w=10, carton_h=10,
            unit_weight=1, pcs_per_carton=1)
        PalletizationInstruction.objects.create(
            product=prod, version=2, name="Import migracji", carton_l=11, carton_w=11,
            carton_h=11, unit_weight=1, pcs_per_carton=1)
        rows = [
            _b("B-UPD", opis="nowa", szt="8 X 8 X 10 cm", kar="40,7 X 28,1 X 60,5 cm",
               kar_qty="750", sup="O013", ean="5901"),
            _b("B-SHORT", short="Krótki opis", kar="30x20x20", kar_qty="abc"),   # pcs → 1
            _b("B-NONAME", szt="1,5 X 2 X 3 cm"),                                # bez kartonu
            _b("B-BADDIM", opis="zły", szt="0 X 5 X 5 cm", kar="brak wymiaru", kar_qty="5"),
            _b("B-OPZ", opis="z opz", szt="2 X 2 X 5 cm", kar="40 X 30 X 25 cm",
               kar_qty="240", op="6 X 4 X 5 cm", op_qty="12",
               opz="20,4 X 15,55 X 11 cm", opz_qty="36"),
            _b("B-OPZ-NOOP", opis="opz bez op", kar="40 X 30 X 25 cm", kar_qty="100",
               opz="20 X 15 X 11 cm", opz_qty="0"),
            _b("B-OPZ-NOCART", opis="opz bez kartonu", opz="20 X 15 X 11 cm", opz_qty="10"),
            # Duplikat kodu w pliku i kolizja po przycięciu do 50 znaków (niżej): w obu
            # przypadkach wygrywa PIERWSZY wiersz, duplikaty zliczone w ostrzeżeniu.
            _b("B-UPD", opis="duplikat ignorowany", kar="1 X 1 X 1 cm", kar_qty="1"),
            _b("", opis="bez kodu"),
            _b("C" * 50 + "X", opis="długi 1", kar="30 X 20 X 20 cm", kar_qty="10"),
            _b("C" * 50 + "Y", opis="długi 2", kar="30 X 20 X 20 cm", kar_qty="12"),
        ]
        self._check("format_b_rich", self._post(B_HEADER + "\n" + "\n".join(rows)))

    def test_format_b_minimal_header_comma_delimited(self):
        # Tylko ref_code + karton_wymiar (ilość pozycyjnie +2); separator ','.
        content = ("ref_code,karton_wymiar,x,qty\n"
                   "M-1,30 x 20 x 10,,6\n"
                   "M-2,,,\n")
        self._check("format_b_minimal", self._post(content))

    def test_format_b_reimport_and_overwrite(self):
        first = [
            _b("OLD-1", opis="stary", kar="30 X 20 X 20 cm", kar_qty="10"),
            _b("OLD-2", opis="stary opz", kar="40 X 30 X 25 cm", kar_qty="240",
               op="6 X 4 X 5 cm", op_qty="12", opz="20 X 15 X 11 cm", opz_qty="36"),
        ]
        m1 = self._post(B_HEADER + "\n" + "\n".join(first))
        # Reimport bez „Nadpisz”: OLD-2 odświeżone (opz aktualizowane), OLD-1 zostaje.
        second = [_b("OLD-2", opis="nowszy", kar="40 X 30 X 25 cm", kar_qty="200",
                     op="6 X 4 X 5 cm", op_qty="10", opz="21 X 15 X 11 cm", opz_qty="40")]
        m2 = self._post(B_HEADER + "\n" + "\n".join(second))
        InnerPack.objects.create(name="RECZNY-opz", length_cm=1, width_cm=1, height_cm=1)
        # „Nadpisz”: znikają wszystkie instrukcje importu i opakowania *-opz utworzone
        # przez import (OLD-2-opz); ręcznie założony „RECZNY-opz” zostaje.
        third = [_b("NEW-1", opis="nowy", kar="30 X 20 X 20 cm", kar_qty="8")]
        m3 = self._post(B_HEADER + "\n" + "\n".join(third), overwrite="1")
        self._check("format_b_reimport_overwrite", [m1, m2, m3])

    # ── format A (SAP MARM) ────────────────────────────────────────────────
    def test_format_a_rich(self):
        Product.objects.create(code="MAT-UPD", name="stara", ean="999", supplier_short="SUP")
        rows = [
            # pełny komplet + duplikat roli (drugi KAR ignorowany) + nieznana jednostka
            _a("MAT-100", "SZT", "1", "1", "8", "8", "10", "0,05", "5901234123457"),
            _a("MAT-100", "KAR", "1", "750", "40,7", "28,1", "60,5", "37,5", "5901234000000"),
            _a("MAT-100", "CTN", "1", "999", "1", "1", "1", "1"),
            _a("MAT-100", "XYZ", "1", "5", "1", "1", "1", "1"),
            _a("MAT-100", "PAZ", "1", "15000", "120", "80", "180", "900"),
            # brak wagi sztuki → waga z KAR / sztuk; EAN z kartonu
            _a("MAT-KW", "ST", "1", "1", "5", "5", "5", "-"),
            _a("MAT-KW", "BOX", "1", "20", "30", "20", "10", "10", "590KAR"),
            _a("MAT-KW", "PLT", "1", "0", "120", "80", "0", "0"),
            # tylko OP + KAR (bez sztuki): waga z OP / sztuk w OP (0,2 kg / 10 = 0,02),
            # spójnie ze ścieżką KAR. MAT-JU: 0,458 / 2 (licznik „1.991” → 2 szt., z ostrzeżeniem).
            _a("MAT-OP", "OPK", "1", "10", "6", "4", "5", "0,2"),
            _a("MAT-OP", "CS", "1", "100", "40", "30", "25", "20"),
            # JU: 100 JU = 1 jedn. bazowa; OP z mianownikiem „1.991”
            _a("MAT-JU", "JU", "100", "1"),
            _a("MAT-JU", "OP", "1", "1.991", "21", "12", "5,5", "0,458", "5900000001609"),
            _a("MAT-JU", "KRT", "1", "10", "29", "25", "22,2", "4,583"),
            _a("MAT-JU", "PL", "1", "1080", "120", "80", "215", "509,964"),
            # OPZ + OPA (op) → InnerPack
            _a("MAT-OPZ", "EA", "1", "1", "2", "2", "5", "0,01"),
            _a("MAT-OPZ", "OPA", "1", "12", "6", "4", "5"),
            _a("MAT-OPZ", "OPZ", "1", "36", "20", "15", "11"),
            _a("MAT-OPZ", "KAR", "1", "240", "40", "30", "25"),
            # istniejący produkt, bez kartonu → aktualizacja bez instrukcji
            # MARM nie ma kolumny dostawcy, EAN pusty → istniejące supplier_short („SUP”)
            # i ean („999”) zostają (puste pole nie nadpisuje).
            _a("MAT-UPD", "PC", "1", "1", "3", "3", "3", "", ""),
            # tylko sztuka o zerowych wymiarach; wiersz bez materiału pomijany
            _a("MAT-ZERO", "SZT", "1", "1", "0", "0", "0"),
            _a("", "SZT", "1", "1", "1", "1", "1"),
            # mianownik 0 → traktowany jak 1; licznik pusty → 0 sztuk
            _a("MAT-MI0", "KAR", "0", "", "10", "10", "10", "5"),
        ]
        self._check("format_a_rich", self._post(A_HEADER + "\n" + "\n".join(rows)))

    def test_format_a_short_aliases(self):
        # Krótkie nazwy kolumn (ajm/umren/umrez/width/height/length/weight/ean).
        content = ("material;ajm;umren;umrez;length;width;height;weight;ean\n"
                   "AL-1;SZT;1;1;1;2;3;0,5;123\n"
                   "AL-1;KAR;2;24;30;20;10;6;\n")
        self._check("format_a_aliases", self._post(content))
