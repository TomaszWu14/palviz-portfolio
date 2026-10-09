"""„Wklej adresy" w Modelu magazynu: wklejona lista adresów jednej alejki/regału →
regały ustawione w linii (poziomo 0° / pionowo 90°), scalanie bezstratne (n_bays/
n_levels nigdy nie maleją), keep_existing chroni pozycje."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui.models import WarehouseModel, WarehouseModelRack


class WarehouseModelPasteTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        self.wm = WarehouseModel.objects.create(name="M", floor_width_m=50, floor_depth_m=30)
        self.url = reverse("ui:warehouse_model_paste", args=[self.wm.pk])

    def _post(self, codes, **extra):
        data = {"codes": codes, "orientation": "horizontal", "start_x": 0, "start_y": 0}
        data.update(extra)
        return self.client.post(self.url, data, follow=True)

    def test_get_renders_form(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Wklej adresy")

    def test_vertical_places_racks_along_y_with_angle_90(self):
        self._post("B0-01-300A\nB0-01-300X\nB0-01-301A\nB0-02-300A",
                   orientation="vertical", start_x=5, start_y=2)
        r1 = WarehouseModelRack.objects.get(model=self.wm, zone="B0", rack_id="01")
        r2 = WarehouseModelRack.objects.get(model=self.wm, zone="B0", rack_id="02")
        # B0-01: boczki {300,301} = 2, poziomy max(A=1, X=2) = 2
        self.assertEqual((r1.n_bays, r1.n_levels), (2, 2))
        self.assertEqual((r2.n_bays, r2.n_levels), (1, 1))
        self.assertEqual(r1.angle_deg, 90)
        self.assertEqual(r2.angle_deg, 90)
        # linia wzdłuż Y: X stałe, Y rośnie o width_m poprzednika
        self.assertEqual(r1.x_m, 5)
        self.assertEqual(r2.x_m, 5)
        self.assertEqual(r1.y_m, 2)
        self.assertEqual(r2.y_m, 2 + r1.width_m)

    def test_horizontal_places_racks_along_x_with_angle_0(self):
        self._post("B0-01-300A\nB0-02-300A", orientation="horizontal", start_x=1, start_y=4)
        r1 = WarehouseModelRack.objects.get(model=self.wm, zone="B0", rack_id="01")
        r2 = WarehouseModelRack.objects.get(model=self.wm, zone="B0", rack_id="02")
        self.assertEqual(r1.angle_deg, 0)
        self.assertEqual(r1.y_m, 4)
        self.assertEqual(r2.y_m, 4)
        self.assertEqual(r1.x_m, 1)
        self.assertEqual(r2.x_m, 1 + r1.width_m)

    def test_merge_existing_never_shrinks_and_no_duplicate(self):
        WarehouseModelRack.objects.create(model=self.wm, zone="B0", rack_id="01",
                                          n_bays=5, n_levels=4, x_m=9, y_m=9)
        self._post("B0-01-300A")          # 1 boczek, 1 poziom — mniej niż istniejące
        racks = WarehouseModelRack.objects.filter(model=self.wm, zone="B0", rack_id="01")
        self.assertEqual(racks.count(), 1)          # bez duplikatu
        self.assertEqual((racks[0].n_bays, racks[0].n_levels), (5, 4))   # nie zmniejszone

    def test_keep_existing_preserves_position(self):
        WarehouseModelRack.objects.create(model=self.wm, zone="B0", rack_id="01",
                                          n_bays=1, n_levels=1, x_m=9, y_m=9, angle_deg=45)
        self._post("B0-01-300A", keep_existing="1", start_x=0, start_y=0)
        r = WarehouseModelRack.objects.get(model=self.wm, zone="B0", rack_id="01")
        self.assertEqual((r.x_m, r.y_m, r.angle_deg), (9, 9, 45))

    def test_without_keep_existing_repositions(self):
        WarehouseModelRack.objects.create(model=self.wm, zone="B0", rack_id="01",
                                          n_bays=1, n_levels=1, x_m=9, y_m=9, angle_deg=45)
        self._post("B0-01-300A", start_x=2, start_y=3)
        r = WarehouseModelRack.objects.get(model=self.wm, zone="B0", rack_id="01")
        self.assertEqual((r.x_m, r.y_m, r.angle_deg), (2, 3, 0))

    def test_lowercase_and_csv_columns_tolerated(self):
        self._post("b0-01-300a,jakiś opis\nB0-01-300X\tinna kolumna")
        r = WarehouseModelRack.objects.get(model=self.wm, zone="B0", rack_id="01")
        self.assertEqual((r.n_bays, r.n_levels), (1, 2))

    def test_shelves_bcd_do_not_add_levels(self):
        # B/C/D to półki w otworze poziomu 1 — nie osobne poziomy (dawniej B=2, C=3, D=4).
        self._post("B0-01-300B\nB0-01-300C\nB0-01-300D\nB0-01-300X\nB0-01-300Y")
        r = WarehouseModelRack.objects.get(model=self.wm, zone="B0", rack_id="01")
        self.assertEqual(r.n_levels, 3)

    def test_garbage_input_errors_and_creates_nothing(self):
        r = self._post("zupelnie nie kod\n???")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseModelRack.objects.filter(model=self.wm).count(), 0)
        self.assertContains(r, "Nie rozpoznano")

    def test_skipped_count_reported(self):
        r = self._post("B0-01-300A\nsmieci\njeszcze smieci")
        self.assertContains(r, "Pominięto 2")
