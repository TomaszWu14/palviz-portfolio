"""PERF-006: strażnicy skalowania liczby zapytań mapy magazynu 3D.

Mapa 3D (`ui:warehouse_map_detail`, HTML i ?fmt=json z ~37k lokalizacji w produkcji),
panel „co stoi w lokalizacji”, „gdzie jest produkt” i lista snapshotów — każdy ekran
mierzony przy 1 i przy 8 wierszach musi robić tyle samo zapytań (testkit/queries.py)."""
from django.test import TestCase
from django.urls import reverse

from testkit import factories as f
from testkit.queries import QueryScalingMixin
from ui.roles import GROUP_MASTER_DATA
from wh3d.models import (WarehouseAisleConfig, WarehouseLayout, WarehouseLayoutCell,
                         WarehouseLocationMaster, WarehouseLocationMasterBatch)


class _MapScreens(QueryScalingMixin, TestCase):
    def setUp(self):
        self.client.force_login(f.UserFactory(username="perf_mapa", groups=[GROUP_MASTER_DATA]))
        self.seq = 0
        self.stock = f.ShipmentFactory(name="Stock magazynu", is_stock=True)

    def _stock_hu(self, location, product=None):
        self.seq += 1
        hu = f.HandlingUnitFactory(shipment=self.stock, location=location, status="planned")
        f.HandlingUnitItemFactory(hu=hu, product=product or f.ProductFactory(),
                                  alt_qty=4, alt_unit="KAR")
        return hu


class MapDetailQueryScalingTests(_MapScreens):
    """Mapa 3D snapshotu: aktywny layout (komórki + konfiguracja alei), master data
    lokalizacji, typ regału, stock HU na lokalizacjach i strefy nietypowe."""

    def setUp(self):
        super().setUp()
        f.RackTypeFactory(code="RT", name="Regał paletowy", manip_mm=900)
        self.layout = WarehouseLayout.objects.create(name="Hala B0", is_active=True)
        WarehouseAisleConfig.objects.create(layout=self.layout, aisle="01", width_m=3.0)
        self.master = WarehouseLocationMasterBatch.objects.create(name="MD", is_active=True)
        self.snap = f.SnapshotFactory()
        self.url = reverse("ui:warehouse_map_detail", args=[self.snap.pk])

    def _grow(self, n):
        for _ in range(n):
            self.seq += 1
            code = f"B0-01-{300 + self.seq}A"
            f.SnapshotRowFactory(snapshot=self.snap, location_code=code, zone="B0", aisle="01",
                                 stack=str(300 + self.seq), col_code="A", level=1,
                                 is_empty=False, capacity_mm=1200, warehouse_type="RT")
            WarehouseLayoutCell.objects.create(layout=self.layout, location_code=code,
                                               grid_row=1, grid_col=self.seq, level=1)
            WarehouseLocationMaster.objects.create(batch=self.master, location_code=code,
                                                   warehouse_type="RT", height_mm=1500,
                                                   width_mm=800)
            self._stock_hu(code)
            # Strefa nietypowa (kod spoza wzorca regału) — osobna ścieżka _special_zones.
            special = f"ZWROTY-{self.seq:02d}"
            f.SnapshotRowFactory(snapshot=self.snap, location_code=special, zone="", aisle="",
                                 stack="", is_empty=False)
            self._stock_hu(special)

    def test_locations_json(self):
        _, _, resp = self.assertQueriesFlat(self.url, self._grow, params={"fmt": "json"})
        self.assertEqual(len(resp.json()["locs"]), 16)         # 8 regałowych + 8 nietypowych

    def test_map_html(self):
        _, _, resp = self.assertQueriesFlat(self.url, self._grow)
        self.assertEqual(resp.context["pallet_stats"]["total"], 16)


class MapSideScreensQueryScalingTests(_MapScreens):
    """Panel lokalizacji (JSON po kliknięciu), „gdzie jest produkt” i lista snapshotów."""

    def test_location_contents_json(self):
        def grow(n):
            for _ in range(n):
                self._stock_hu("B0-01-300A")
        _, _, resp = self.assertQueriesFlat(reverse("ui:warehouse_location_contents"), grow,
                                            params={"code": "B0-01-300A"})
        self.assertEqual(resp.json()["count"], 8)

    def test_where_is(self):
        def grow(n):
            for _ in range(n):
                self._stock_hu(f"B0-02-{400 + self.seq}A",
                               product=f.ProductFactory(code=f"SZUK{self.seq:04d}"))
        _, _, resp = self.assertQueriesFlat(reverse("ui:warehouse_where_is"), grow,
                                            params={"q": "SZUK"})
        self.assertEqual(len(resp.context["rows"]), 8)

    def test_snapshot_list(self):
        def grow(n):
            for _ in range(n):
                f.SnapshotFactory(row_count=12)
                WarehouseLayout.objects.create(name=f"Layout {self.seq}", location_count=3)
                WarehouseLocationMasterBatch.objects.create(name=f"MD {self.seq}")
                self.seq += 1
        # Lista pokazuje max 10 snapshotów i po 5 layoutów/batchy — 5 wierszy mieści się we
        # wszystkich trzech limitach, więc „dużo” naprawdę znaczy więcej wierszy na ekranie.
        _, _, resp = self.assertQueriesFlat(reverse("ui:warehouse_map"), grow, many=5)
        self.assertEqual(len(resp.context["snapshots"]), 5)
