"""Model magazynu z pliku geometrii (CSV z rysunku hali): parser + upload tworzący regały
od razu z pozycjami; błędny wiersz = żaden model nie powstaje."""
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ui.models import WarehouseModel
from wh3d.blender_route import rack_corners
from wh3d.model_geometry import floor_size, is_geometry_csv, parse_geometry_csv

CSV = ("zone;rack_id;x_m;y_m;angle_deg;n_bays;bay_width_cm;depth_cm;n_levels\n"
       "B0;01;10,5;5;-90;21;282;103;4\n"
       "B0;02;7;64;90;21;282;103;\n")


class GeometryParserTests(SimpleTestCase):
    def test_detects_geometry_header_not_location_codes(self):
        self.assertTrue(is_geometry_csv(CSV))
        self.assertFalse(is_geometry_csv("B0-01-300A\nB0-01-300B\n"))

    def test_parses_rows_with_decimal_comma_and_defaults(self):
        racks, errors = parse_geometry_csv(CSV)
        self.assertEqual(errors, [])
        self.assertEqual(racks[0], {"zone": "B0", "rack_id": "01", "x_m": 10.5, "y_m": 5.0,
                                    "angle_deg": -90.0, "n_bays": 21, "bay_width_cm": 282,
                                    "depth_cm": 103, "n_levels": 4, "level_height_cm": 200})
        self.assertEqual(racks[1]["n_levels"], 4)          # puste pole → domyślna wartość

    def test_reports_bad_rows_missing_columns_and_duplicates(self):
        _, errors = parse_geometry_csv("zone;rack_id;x_m\nB0;01;1\n")
        self.assertIn("Brak kolumn", errors[0])
        _, errors = parse_geometry_csv(CSV + "B0;03;abc;1;0;5;270;110;4\nB0;01;1;1;0;5;270;110;4\n")
        self.assertEqual(len(errors), 2)
        self.assertIn("Linia 4", errors[0])
        self.assertIn("powtórzony", errors[1])

    def test_floor_fits_rotated_racks(self):
        racks, _ = parse_geometry_csv(CSV)
        # regał 01: kąt −90° → rośnie w +y od y=5 na 21×2,82 m = 59,22 m; +2 m marginesu
        width, depth = floor_size(racks, rack_corners)
        self.assertEqual((width, depth), (12.5, 66.2))


class GeometryUploadTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="g", password="x")
        self.client.post("/login/", {"username": "g", "password": "x"})

    def _upload(self, text):
        f = SimpleUploadedFile("geometria.csv", text.encode("utf-8"), content_type="text/csv")
        return self.client.post(reverse("ui:warehouse_model_upload"),
                                {"name": "Hala z rysunku", "floor_width_m": 50, "floor_depth_m": 30,
                                 "location_file": f}, follow=True)

    def test_creates_model_with_positioned_racks_and_grows_floor(self):
        r = self._upload(CSV)
        self.assertEqual(r.status_code, 200)
        wm = WarehouseModel.objects.get(name="Hala z rysunku")
        rack = wm.racks.get(zone="B0", rack_id="01")
        self.assertEqual((rack.x_m, rack.y_m, rack.angle_deg), (10.5, 5.0, -90.0))
        self.assertEqual((rack.n_bays, rack.n_levels, rack.bay_width_cm, rack.depth_cm), (21, 4, 282, 103))
        self.assertEqual((wm.floor_width_m, wm.floor_depth_m), (50, 66.2))   # szerokość z formularza większa

    def test_view_2d_rotation_matches_3d_and_lite_steel_for_big_halls(self):
        self._upload(CSV)
        wm = WarehouseModel.objects.get(name="Hala z rysunku")
        r = self.client.get(reverse("ui:warehouse_model_view", args=[wm.pk]))
        # SVG (y w dół) obraca odwrotnie niż three.js rotation.y — bez minusa regał pod 90°
        # w planie 2D był lustrzanym odbiciem widoku 3D.
        self.assertContains(r, "rotate(${-r.angle})")
        self.assertContains(r, "rotate(${-f.angle})")
        self.assertContains(r, "LITE_STEEL")

    def test_bad_row_creates_nothing(self):
        r = self._upload(CSV + "B0;03;abc;1;0;5;270;110;4\n")
        self.assertContains(r, "Linia 4")
        self.assertFalse(WarehouseModel.objects.exists())
