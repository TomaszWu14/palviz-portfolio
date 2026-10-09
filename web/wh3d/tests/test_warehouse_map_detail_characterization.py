"""Testy charakteryzacyjne widoku ``warehouse_map_detail`` (CODE-001).

Przypinają AKTUALNE zachowanie (kontekst HTML + payload ``?fmt=json``) dla
reprezentatywnych scenariuszy — strażnik przed refaktorem (rozbicie funkcji CC=84).
Oczekiwane wartości leżą w ``data/warehouse_map_detail_snapshot.json``.
Regeneracja (TYLKO świadomie, gdy zmiana zachowania jest zamierzona):
``WH3D_UPDATE_SNAPSHOT=1 python manage.py test wh3d.tests.test_warehouse_map_detail_characterization``
"""
import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from testkit.personas import client_for
from ui import models as m

SNAPSHOT_FILE = Path(__file__).parent / "data" / "warehouse_map_detail_snapshot.json"
_UPDATE = os.environ.get("WH3D_UPDATE_SNAPSHOT") == "1"
_collected = {}


def _norm(value):
    """JSON-owa normalizacja (krotki → listy, klucze → str) do porównania ze snapshotem."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _strip_ids(value):
    """Klucze PK zależą od sekwencji bazy (PostgreSQL ich nie resetuje między testami)."""
    if isinstance(value, dict):
        return {k: _strip_ids(v) for k, v in value.items() if k != "id"}
    if isinstance(value, list):
        return [_strip_ids(v) for v in value]
    return value


def _row(snap, code, **kw):
    parts = code.split("-")
    defaults = {"zone": parts[0] if len(parts) > 1 else "",
                "aisle": parts[1] if len(parts) > 1 else "",
                "stack": parts[2][:-1] if len(parts) > 2 else "",
                "col_code": "A", "col_idx": 0, "level": 1, "is_empty": True,
                "capacity_mm": 0}
    defaults.update(kw)
    return m.WarehouseSnapshotRow.objects.create(snapshot=snap, location_code=code, **defaults)


def _hu(code_seq, location, stock=True):
    ship, _ = m.Shipment.objects.get_or_create(name=f"S-{stock}", is_stock=stock)
    m.HandlingUnit.objects.create(shipment=ship, seq=code_seq, code=f"H{code_seq}",
                                  location=location)


def _rich_rows(snap):
    """Wiersze pokrywające wszystkie gałęzie pętli lokalizacji: 4 stany (wolna/zajęta/
    zablokowana zajęta/zablokowana pusta), antresola, kody nietypowe, rozkład pojemności,
    wysokość z master data / z poziomu typu / z capacity, sufiks stosu."""
    _row(snap, "B0-01-300A", is_empty=False, capacity_mm=0, warehouse_type="RT")
    _row(snap, "B0-01-300B", col_code="B", col_idx=1, is_empty=True, capacity_mm=500,
         warehouse_type="RT", level=2)
    _row(snap, "B0-01-301A", is_empty=False, blocked_pick=True, capacity_mm=1000,
         warehouse_type="RT")
    _row(snap, "B0-02-300A", is_empty=True, blocked_put=True, capacity_mm=1500,
         warehouse_type="XX")
    _row(snap, "B0-02-302C-1", stack="302C", col_code="C", col_idx=2, is_empty=False,
         capacity_mm=2500, warehouse_type="RT", level=3)
    _row(snap, "B0-55-300A", aisle="55", is_empty=False, capacity_mm=1200, warehouse_type="RT")
    _row(snap, "A0-01-100A", is_empty=False, capacity_mm=700)            # antresola
    _row(snap, "ZWROTY-01", zone="", aisle="", stack="", is_empty=False)  # nietypowa
    _row(snap, "04.01", zone="", aisle="04", stack="01", warehouse_type="92EX",
         blocked_pick=True, capacity_mm=2000)
    # Stock HU: na mapie, poza mapą, na antresoli, w strefie nietypowej; non-stock ignorowane.
    _hu(1, "B0-01-300A")
    _hu(2, " b0-01-300a ")
    _hu(3, "ZZ-99-999Z")
    _hu(4, "A0-01-100A")
    _hu(5, "ZWROTY-01")
    _hu(6, "B0-01-300A", stock=False)


def _rack_types():
    m.WarehouseRackType.objects.create(
        code="RT", name="Typ", manip_mm=900, width_mm=800, depth_mm=1100,
        max_weight_kg=1000, max_volume_m3=2.5, color_hex="#112233",
        level_heights={"1": 1800, "2": "1300", "3": ""}, level_cols={"1": "3", "2": 2},
        level_weights={"1": 1000, "2": "300"})
    m.WarehouseRackType.objects.create(code="UNUSED", name="Nieużywany", manip_mm=500)


def _master(width_mm=0):
    batch = m.WarehouseLocationMasterBatch.objects.create(name="MD", is_active=True)
    m.WarehouseLocationMaster.objects.create(
        batch=batch, location_code="B0-01-300B", warehouse_type="RT", height_mm=1111,
        width_mm=width_mm, max_volume_m3=9.5, max_weight_kg=777)
    m.WarehouseLocationMaster.objects.create(
        batch=batch, location_code="B0-02-300A", warehouse_type="", height_mm=0)
    return batch


def _layout_cells(layout, specs):
    for code, row, col, lvl in specs:
        m.WarehouseLayoutCell.objects.create(layout=layout, location_code=code,
                                             grid_row=row, grid_col=col, level=lvl)


class MapDetailCharacterizationTests(TestCase):
    maxDiff = None

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if _UPDATE and _collected:
            data = json.loads(SNAPSHOT_FILE.read_text("utf-8")) if SNAPSHOT_FILE.exists() else {}
            data.update(_collected)
            SNAPSHOT_FILE.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False)
                                     + "\n", "utf-8")

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("char", password="x")
        self.client.force_login(self.user)
        self.snap = m.WarehouseSnapshot.objects.create(name="Snap", row_count=99,
                                                       occupied_count=50, blocked_count=7)
        self.url = reverse("ui:warehouse_map_detail", args=[self.snap.pk])

    def _capture(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        ctx = r.context
        out = {k: _norm(ctx[k]) for k in (
            "pallet_stats", "use_physical", "from_layout", "slot_width_mm", "stats",
            "special_zones", "hall_features", "rack_types_ctx", "aisle_meta",
            "aisle_stats", "cap_groups", "loc_count")}
        self.assertEqual(ctx["snapshot"], self.snap)
        out["active_layout"] = getattr(ctx["active_layout"], "name", None)
        out["active_master"] = getattr(ctx["active_master"], "name", None)
        rj = self.client.get(self.url, {"fmt": "json"})
        self.assertEqual(rj.status_code, 200)
        self.assertEqual(rj["Content-Type"], "application/json")
        out["json"] = rj.json()
        return out

    def _check(self, name):
        got = self._capture()
        if _UPDATE:
            _collected[name] = got
            return
        expected = json.loads(SNAPSHOT_FILE.read_text("utf-8"))[name]
        self.assertEqual(_strip_ids(got), _strip_ids(expected))

    # ── scenariusze ────────────────────────────────────────────────────────
    def test_empty_snapshot_no_layout(self):
        self._check("empty_no_layout")

    def test_empty_snapshot_with_layout_and_config(self):
        lay = m.WarehouseLayout.objects.create(name="L", is_active=True)
        m.WarehouseAisleConfig.objects.create(layout=lay, aisle="01", width_m=3.0, angle_deg=90)
        self._check("empty_with_layout")

    def test_code_derived_rich(self):
        _rack_types()
        _master()
        _rich_rows(self.snap)
        self._check("code_derived_rich")

    def test_code_derived_master_width_and_aisle_cfg(self):
        # Layout bez komórek → pozycje z kodów; szerokości/kąty alei z konfiguracji,
        # slot_width_mm z master data (ma pierwszeństwo przed manip_mm typów).
        _rack_types()
        _master(width_mm=1000)
        _rich_rows(self.snap)
        lay = m.WarehouseLayout.objects.create(name="Pusty", is_active=True)
        m.WarehouseAisleConfig.objects.create(layout=lay, aisle="01", width_m=3.0)
        m.WarehouseAisleConfig.objects.create(layout=lay, aisle="55", width_m=2.0, angle_deg=90)
        m.WarehouseHallFeature.objects.create(layout=lay, kind="returns", label="Zwroty",
                                              zone_code="ZWROTY", x_m=1, y_m=2)
        self._check("code_derived_cfg")

    def _physical_layout(self, name="Fiz"):
        lay = m.WarehouseLayout.objects.create(name=name, is_active=True)
        _layout_cells(lay, [
            ("B0-01-300A", 0, 0, 1), ("B0-01-300X", 0, 0, 2), ("B0-01-301A", 0, 3, 1),
            ("B0-02-300A", 2, 0, 1), ("B0-02-302A", 2, 6, 1), ("B0-55-300A", 4, 0, 1),
        ])
        return lay

    def test_physical_layout_plain(self):
        _rack_types()
        _rich_rows(self.snap)
        self._physical_layout()
        self._check("layout_plain")

    def test_physical_layout_widths_and_wing(self):
        _rack_types()
        _rich_rows(self.snap)
        lay = self._physical_layout()
        m.WarehouseAisleConfig.objects.create(layout=lay, aisle="01", width_m=3.6)
        m.WarehouseAisleConfig.objects.create(layout=lay, aisle="55", width_m=2.0, angle_deg=90)
        m.WarehouseHallFeature.objects.create(layout=lay, kind="dock", label="Dok 1",
                                              x_m=5, y_m=6, width_m=3, depth_m=2)
        self._check("layout_widths_wing")

    def test_physical_layout_wing_only_no_slot_width(self):
        # Brak typów → slot_width_mm=0 → szerokości ignorowane, sam kąt działa.
        _rich_rows(self.snap)
        lay = self._physical_layout()
        m.WarehouseAisleConfig.objects.create(layout=lay, aisle="55", width_m=2.0, angle_deg=90)
        self._check("layout_wing_only")

    def test_layout_not_covering_snapshot_falls_back(self):
        # Layout pokrywa <50% par (aleja, stos) → pozycje z kodów.
        _rich_rows(self.snap)
        lay = m.WarehouseLayout.objects.create(name="Mały", is_active=True)
        _layout_cells(lay, [("B0-01-300A", 0, 0, 1)])
        self._check("layout_not_covering")

    def test_only_nonrack_rows(self):
        _row(self.snap, "ZWROTY-01", zone="", aisle="", stack="", is_empty=False)
        _row(self.snap, "DOK", zone="", aisle="", stack="")
        self._check("only_nonrack")

    # ── strażnik / brak obiektu ───────────────────────────────────────────
    def test_missing_snapshot_404(self):
        r = self.client.get(reverse("ui:warehouse_map_detail", args=[self.snap.pk + 999]))
        self.assertEqual(r.status_code, 404)

    def test_guard_anon_and_no_role(self):
        for suffix in ("", "?fmt=json"):
            r = client_for("anon").get(self.url + suffix)
            self.assertEqual(r.status_code, 302)
            self.assertEqual(client_for("bez_roli").get(self.url + suffix).status_code, 403)
