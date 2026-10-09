from django.contrib.auth import get_user_model
from django.test import TestCase
from ui.models import WarehouseLayout, WarehouseLayoutCell, WarehouseLocationMasterBatch

class WarehouseBuilderTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="boss", password="secret123")
        self.client.post("/login/", {"username": "boss", "password": "secret123"})

    def test_demo_seed_builds_active_warehouse(self):
        r = self.client.post("/magazyn/demo-seed/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/editor3d", r.url)
        layout = WarehouseLayout.objects.get(is_active=True)
        # 3 aisles x 6 stacks x 3 cols x 4 levels = 216
        self.assertEqual(layout.cells.count(), 216)
        # aisles separated into distinct grid_row bands (0,2,4)
        rows = set(WarehouseLayoutCell.objects.filter(layout=layout).values_list("grid_row", flat=True))
        self.assertEqual(rows, {0, 2, 4})
        self.assertTrue(WarehouseLocationMasterBatch.objects.filter(is_active=True).exists())

    def test_generator_get_requires_md_role(self):
        self.assertEqual(self.client.get("/magazyn/rack-generator/").status_code, 200)

    def test_append_adds_to_active_layout_without_overwrite(self):
        # first rack: fresh
        base = {"name":"R1","zone":"B0","aisle":"01","stack_start":"100","stack_count":"4",
                "stack_step":"2","columns":"A,B","num_levels":"3","slot_width":"800","slot_depth":"1100",
                "level_1_height":"2500","level_2_height":"2500","level_3_height":"2500"}
        self.client.post("/magazyn/rack-generator/", base)
        l1 = WarehouseLayout.objects.get(is_active=True)
        n1 = l1.cells.count()                       # 4*2*3 = 24
        self.assertEqual(n1, 24)
        # second rack appended
        base2 = dict(base, name="R2", aisle="02", append="1")
        self.client.post("/magazyn/rack-generator/", base2)
        # still the SAME active layout, now larger
        self.assertEqual(WarehouseLayout.objects.filter(is_active=True).count(), 1)
        l1.refresh_from_db()
        self.assertEqual(l1.cells.count(), 48)
        rows = set(l1.cells.values_list("grid_row", flat=True))
        self.assertEqual(rows, {0, 2})              # appended aisle on its own row band

    def test_generator_tolerates_nonnumeric_input(self):
        # non-numeric values must not 500 — they fall back to defaults
        bad = {"name": "Bad", "zone": "B0", "aisle": "01",
               "stack_start": "abc", "stack_count": "xx", "stack_step": "",
               "num_levels": "lots", "max_weight": "heavy", "max_volume": "n/a",
               "columns": "A,B", "slot_width": "wide", "slot_depth": "deep"}
        r = self.client.post("/magazyn/rack-generator/", bad)
        self.assertEqual(r.status_code, 302)  # generated with defaults, no crash
        from ui.models import WarehouseLayout
        self.assertTrue(WarehouseLayout.objects.filter(is_active=True).exists())

    def test_editor_renders_locations_json_script(self):
        # regression: json_script tag was outside {% block %} → element missing → JS crash
        from ui.models import WarehouseLayout, WarehouseLayoutCell
        layout = WarehouseLayout.objects.create(name="L", is_active=True)
        WarehouseLayoutCell.objects.create(layout=layout, location_code="B0-01-100-1A",
                                            grid_row=0, grid_col=0, level=1)
        r = self.client.get("/magazyn/editor/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="id_editor_locs"')   # data script present in rendered HTML

    def test_build_stack_base_parses_subshelf_codes(self):
        # regression: B0-07-300C-1 was mis-parsed as stack '300C' / col '1'
        from ui.models import WarehouseLayout, WarehouseLayoutCell
        from ui.views import _build_stack_base
        lay = WarehouseLayout.objects.create(name="P", is_active=True)
        WarehouseLayoutCell.objects.create(layout=lay, location_code="B0-07-300C-1",
                                            grid_row=36, grid_col=165, level=1)
        WarehouseLayoutCell.objects.create(layout=lay, location_code="B0-01-100A",
                                            grid_row=6, grid_col=10, level=1)
        base = _build_stack_base(lay)
        self.assertIn(("07", "300"), base)        # correct stack key
        self.assertNotIn(("07", "300C"), base)    # not the mis-parsed key
        self.assertIn(("01", "100"), base)
