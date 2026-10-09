"""Testy charakteryzacyjne widoku ``warehouse_combined_upload`` (CODE-001).

Przypinają AKTUALNE zachowanie kombinowanego importu (layout + master + typy regałów)
z xlsx i csv: wykrywanie nagłówka, konwersje, typy regałów, backfill wymiarów ze słownika,
stan bazy, komunikaty i przekierowanie — strażnik przed refaktorem (rozbicie funkcji
CC=46). Oczekiwane wartości leżą w ``data/warehouse_combined_upload_snapshot.json``; PK
są pomijane (sekwencje PostgreSQL w CI nie resetują się między testami) — komórki
i rekordy master wiążemy z layoutem / batchem przez nazwę.
Regeneracja (TYLKO świadomie, gdy zmiana zachowania jest zamierzona):
``WH3D_COMBINED_UPDATE_SNAPSHOT=1 python manage.py test wh3d.tests.test_warehouse_combined_upload_characterization``
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

from ui.models import (WarehouseLayout, WarehouseLayoutCell, WarehouseLocationMaster,
                       WarehouseLocationMasterBatch, WarehouseRackType)
from ui.roles import GROUP_MASTER_DATA

SNAPSHOT_FILE = Path(__file__).parent / "data" / "warehouse_combined_upload_snapshot.json"
_UPDATE = os.environ.get("WH3D_COMBINED_UPDATE_SNAPSHOT") == "1"
_collected = {}

URL_NAME = "ui:warehouse_combined_upload"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LIMIT = 30 * 1024 * 1024

# Typy regałów obserwowane w snapshocie (reszta słownika z migracji 0174 nas nie dotyczy).
_RT_CODES = ("0010", "0011", "WCGL", "NEW1", "NEW2", "HONLY", "JUNK")


def _xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csv(rows, bom=True):
    text = "\n".join(",".join(str(c) for c in r) for r in rows) + "\n"
    return (("﻿" if bom else "") + text).encode("utf-8")


def _norm(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


_CELL_FIELDS = ("location_code", "grid_row", "grid_col", "level")
_MASTER_FIELDS = ("location_code", "level", "warehouse_type", "height_mm", "width_mm",
                  "depth_mm", "max_volume_m3", "max_weight_kg")
_RT_FIELDS = ("code", "name", "kind", "width_mm", "depth_mm", "max_weight_kg",
              "max_volume_m3", "level_heights", "level_cols")


def _db_state():
    """Stan bazy bez PK — komórki/master przypięte do layoutu/batcha przez nazwę."""
    layouts = list(WarehouseLayout.objects.order_by("name")
                   .values("name", "location_count", "is_active"))
    cells = [dict(zip(("layout",) + _CELL_FIELDS, r, strict=True)) for r in
             WarehouseLayoutCell.objects.order_by("layout__name", "location_code")
             .values_list("layout__name", *_CELL_FIELDS)]
    batches = list(WarehouseLocationMasterBatch.objects.order_by("name")
                   .values("name", "location_count", "is_active"))
    masters = [dict(zip(("batch",) + _MASTER_FIELDS, r, strict=True)) for r in
               WarehouseLocationMaster.objects.order_by("batch__name", "location_code")
               .values_list("batch__name", *_MASTER_FIELDS)]
    rack_types = list(WarehouseRackType.objects.filter(code__in=_RT_CODES).order_by("code")
                      .values(*_RT_FIELDS))
    return _norm({"layouts": layouts, "cells": cells, "batches": batches,
                  "masters": masters, "rack_types": rack_types})


class WarehouseCombinedUploadCharacterizationTests(TestCase):
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
        u = get_user_model().objects.create_user(username="wh-comb", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)
        self.layout = WarehouseLayout.objects.create(name="Hala", is_active=True)
        # Słownik: 0010 = regał z wymiarami (backfill), WCGL = strefa (bez geometrii).
        rt = WarehouseRackType.objects.get(code="0010")
        rt.width_mm, rt.depth_mm = 800, 1100
        rt.max_weight_kg, rt.max_volume_m3 = 1200, 2.5
        rt.level_heights = {"1": 2494, "3": 1800}
        rt.save()
        rt11 = WarehouseRackType.objects.get(code="0011")
        rt11.level_heights = {}
        rt11.width_mm, rt11.depth_mm, rt11.max_weight_kg, rt11.max_volume_m3 = 0, 0, 0, 0
        rt11.save()

    # ── narzędzia ──────────────────────────────────────────────────────────
    def _post(self, content=None, filename="c.csv", ctype="text/csv", layout_pk=None):
        data = {}
        if content is not None:
            data["file"] = SimpleUploadedFile(filename, content, content_type=ctype)
        pk = self.layout.pk if layout_pk is None else layout_pk
        resp = self.client.post(reverse(URL_NAME, args=[pk]), data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("ui:warehouse_map"))
        msgs = [[m.level_tag, m.message] for m in get_messages(resp.wsgi_request)]
        return {"messages": msgs}

    def _check(self, name, result):
        got = dict(result, db=_db_state())
        if _UPDATE:
            _collected[name] = got
            return
        expected = json.loads(SNAPSHOT_FILE.read_text("utf-8"))[name]
        self.assertEqual(got, expected)

    def _assert_read_error(self, res):
        self.assertEqual(len(res["messages"]), 1)
        self.assertEqual(res["messages"][0][0], "error")
        self.assertTrue(res["messages"][0][1].startswith("Błąd odczytu pliku: "))

    # ── walidacja wejścia / uprawnienia ────────────────────────────────────
    def test_no_file(self):
        self._check("no_file", self._post())

    def test_file_too_large(self):
        self._check("too_large", self._post(b"x" * (LIMIT + 1)))

    def test_file_at_size_limit_is_processed(self):
        # Dokładnie 30 MB przechodzi bramkę rozmiaru (warunek „>”). Dopełnienie wierszami
        # samych spacji (< limitu pola csv) — lokalizacja po strip() pusta → pomijane.
        body = _csv([["Lokalizacja"], ["B0-01-100A"]])
        pad_line = b" " * 99_999 + b"\n"
        full, rest = divmod(LIMIT - len(body), len(pad_line))
        body += pad_line * full + b" " * rest
        self.assertEqual(len(body), LIMIT)
        self._check("at_size_limit", self._post(body))

    def test_broken_xlsx_reports_read_error(self):
        res = self._post(b"not-a-zip", filename="plik.xlsx", ctype=XLSX)
        self._assert_read_error(res)
        self._check("broken_xlsx", dict(res, messages=[]))

    def test_non_csv_extension_goes_through_openpyxl(self):
        # Każde rozszerzenie inne niż .csv (także .txt z treścią CSV) idzie do openpyxl
        # → „Błąd odczytu pliku”. Obecne zachowanie, przypięte.
        res = self._post(b"Lokalizacja\nB0-01-100A\n", filename="plik.txt")
        self._assert_read_error(res)
        self.assertFalse(WarehouseLayoutCell.objects.exists())

    def test_empty_csv(self):
        self._check("empty_csv", self._post(b""))

    def test_empty_xlsx(self):
        self._check("empty_xlsx", self._post(_xlsx([]), filename="e.xlsx", ctype=XLSX))

    def test_no_location_header(self):
        self._check("no_loc_header", self._post(_csv([["Coś", "Ilość"], ["a", "1"]])))

    def test_header_only_no_data(self):
        self._check("header_only", self._post(_csv([["Location", "Type"], ["", "X"]])))

    def test_csv_not_utf8_is_decoded_with_replacement(self):
        # CSV czytany z errors="replace" — cp1250 nie wywala importu; znaki ł/ó → „�”,
        # przez co nagłówek „Miejsce składowania” nadal łapie alias „miejsce”.
        body = "Miejsce składowania;x\nB0-01-100A\n".encode("cp1250")
        self._check("csv_cp1250", self._post(body))

    def test_infinity_in_int_column_crashes(self):
        # BŁĄD (obecne zachowanie): _parse_int łapie tylko ValueError/TypeError, a
        # int(float("inf")) rzuca OverflowError → 500 zamiast pominięcia/zera. Przypięte.
        body = _csv([["Lokalizacja", "Height"], ["B0-01-100A", "inf"]])
        with self.assertRaises(OverflowError):
            self._post(body)
        self.assertFalse(WarehouseLayoutCell.objects.exists())

    def test_csv_field_over_csv_limit_crashes(self):
        # BŁĄD (obecne zachowanie): odczyt CSV nie jest w try/except (w odróżnieniu od xlsx),
        # a pole > 131072 znaków rzuca _csv.Error → 500 zamiast „Błąd odczytu pliku”. Przypięte.
        import csv
        body = _csv([["Lokalizacja"], ["B0-01-100A"]]) + b"x" * 131_073 + b"\n"
        with self.assertRaises(csv.Error):
            self._post(body)
        self.assertFalse(WarehouseLayoutCell.objects.exists())

    def test_guard_and_method(self):
        url = reverse(URL_NAME, args=[self.layout.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(reverse(URL_NAME, args=[999999])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)          # anon → logowanie
        self.client.force_login(get_user_model().objects.create_user(username="nobody"))
        self.assertEqual(self.client.post(url).status_code, 403)          # bez roli
        self.assertFalse(WarehouseLocationMasterBatch.objects.exists())

    # ── pełny import (csv) ─────────────────────────────────────────────────
    def test_csv_full_new_batch(self):
        header = ["Lokalizacja", "Typ magazynu", "Wysokość", "Szerokość", "Głębokość",
                  "Maksymalna waga", "Maksymalna objętość", "level_heights", "level_cols"]
        lh = '"{""1"": 50, ""2"": ""abc"", ""3"": 200000}"'
        lc = '"{""1"": 9, ""2"": 0}"'
        rows = [
            ["Raport lokalizacji magazynu"],            # tytuł bez aliasu — pomijany przy szukaniu nagłówka
            header,
            # Nowy typ NEW1 z wymiarami + JSON poziomów (przycięty do zakresów).
            ["B0-01-100A", "NEW1", "1500", "900", "1000", '"1000,5"', '"2,5"', lh, lc],
            # Drugi wiersz NEW1 — typ bierze PIERWSZE wystąpienie.
            ["B0-01-100B", "NEW1", "1600", "950", "1050", "", "", "", ""],
            # Duplikat kodu — pomijany.
            ["B0-01-100A", "NEW1", "9999", "", "", "", "", "", ""],
            # Kolumny X/Y/Z → poziomy 2/3/4; słownik 0010 wypełnia wymiary (poziom 3 → 1800).
            ["B0-01-101Y", "0010", "", "", "", "", "", "", ""],
            # Poziom 2 brak w level_heights → max(level_heights).
            ["B0-01-101X", "0010", "", "", "", "", "", "", ""],
            # Strefa WCGL: bez geometrii ze słownika, ale JSON poziomów aktualizuje typ.
            ["GLRP", "WCGL", "", "", "", "", "", '"[1, 2]"', '"{""1"": 2}"'],
            # Typ tylko z wysokością → powstaje z domyślnymi wymiarami MODELU (800/1100/1200/2.5),
            # a backfill master bierze te domyślne jak dane słownika. Obecne zachowanie.
            ["B0-02-200C", "HONLY", "2100", "", "", "", "", "", ""],
            # JSON-śmieci → None (typ JUNK nie ma wymiarów → nie powstaje).
            ["B0-02-201", "JUNK", "", "", "", "", "", "{bad", '"{""a"": ""x""}"'],
            # 4-częściowy kod buildera z jawnym poziomem.
            ["B0-03-300-2K", "", "abc", "12.7", "", "", "", "", ""],
            # Kody nieparsowalne → aleja „00”, stos „0”, kol. 0 — ta sama pozycja siatki.
            ["ZWROTY", "", "", "", "", "", "", "", ""],
            ["DOK1", "", "", "", "", "", "", "", ""],
            # Pusta lokalizacja — pomijana; krótki wiersz bez kolumn wymiarów.
            ["", "NEW2", "100", "", "", "", "", "", ""],
            ["B0-04-100A"],
            [],
        ]
        self._check("csv_full_new_batch", self._post(_csv(rows), filename="Combined.CSV"))

    def test_csv_english_aliases_updates_existing(self):
        # Istniejący layout (komórka) + aktywny batch (rekord) → aktualizacje, nie duplikaty.
        WarehouseLayoutCell.objects.create(layout=self.layout, location_code="B0-01-100A",
                                           grid_row=9, grid_col=9, level=9)
        other = WarehouseLayout.objects.create(name="Inny", is_active=True)
        WarehouseLayoutCell.objects.create(layout=other, location_code="B0-01-100A",
                                           grid_row=1, grid_col=1, level=1)
        WarehouseLocationMasterBatch.objects.create(name="Stary", is_active=False)
        active = WarehouseLocationMasterBatch.objects.create(name="Aktywny", is_active=True)
        WarehouseLocationMaster.objects.create(batch=active, location_code="B0-01-100A",
                                               warehouse_type="OLD", height_mm=1)
        WarehouseRackType.objects.create(code="NEW2", name="Czytelna nazwa", width_mm=1,
                                         depth_mm=2)
        rows = [
            ["Bin", "Rack_Type", "Height_mm", "Width", "Depth", "Max_Weight", "Max_Volume"],
            ["B0-01-100A", "NEW2", "1200", "700", "", "10", "1.5"],
            ["B0-01-100B", "", "", "", "", "", ""],
            ["B1-01-100A", "NEW2", "", "", "", "", ""],
            ["B0-02-100A", "", "", "", "", "", ""],
            ["B0-01-105C", "", "", "", "", "", ""],
        ]
        self._check("csv_english_update", self._post(_csv(rows, bom=False)))

    # ── xlsx ───────────────────────────────────────────────────────────────
    def test_xlsx_numeric_cells(self):
        rows = [
            [None, None],
            ["Adres", "Typ", "Wysokosc", "Szerokosc", "Glebokosc", "Waga", "Objetosc",
             "Wysokosci_poziomow", "Kolumny_poziomow"],
            ["B0-01-100A", 11, 1500.7, 800, 1100, 999.5, 1.25, '{"1": 1000}', '{"1": 2}'],
            ["B0-01-100B", "0011", None, None, None, None, None, None, None],
            [101, None, None, None, None, None, None, None, None],
            [None, "0011", 5, None, None, None, None, None, None],
            [0, None, None, None, None, None, None, None, None],
        ]
        self._check("xlsx_numeric", self._post(_xlsx(rows), filename="c.xlsx", ctype=XLSX))
