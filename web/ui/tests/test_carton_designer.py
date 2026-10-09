"""Tests for the carton designer (live model on /planner/cartons/new/)."""
import io
import json
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.models import Carton, CartonArtwork, WarehouseLocationType
from ui.roles import GROUP_MASTER_DATA


def _png_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 120, 60)).save(buf, format="PNG")
    return buf.getvalue()


def _md_user():
    User = get_user_model()
    u = User.objects.create_user(username="md", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
    return u


class CartonDesignerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _md_user()
        # defined warehouse location types feed the carton designer dropdown
        WarehouseLocationType.objects.create(
            name="A", location_class="pallet_full", is_pallet_location=True,
            width_cm=80, depth_cm=120, total_height_cm=235, max_load_kg=1000)
        WarehouseLocationType.objects.create(
            name="C-1,C-2", location_class="shelf", is_pallet_location=False,
            width_cm=43, depth_cm=100, total_height_cm=52)

    def test_new_carton_form_renders(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("ui:planner_carton_new"))
        self.assertEqual(resp.status_code, 200)
        # defined locations injected as json_script for the dropdown
        self.assertContains(resp, "dim-groups-data")
        self.assertContains(resp, "C-1,C-2")     # named location reaches the page
        self.assertContains(resp, "Model na żywo")
        # redesigned live-model UI pieces
        self.assertContains(resp, "Podpowiedź systemu — ranking lokalizacji")
        self.assertContains(resp, 'id="live-maxh"')          # max-height slider
        self.assertContains(resp, "Kartony łącznie")          # new KPI tile
        self.assertContains(resp, 'id="live-animate"')        # 3D animate button

    def test_create_carton_with_layout(self):
        self.client.force_login(self.user)
        layout = {
            "version": 1,
            "carton": {"l": 40, "w": 30, "h": 20},
            "location": {"w_cm": 80, "d_cm": 120, "h_cm": 150, "is_pallet": True, "label": "80×120×150 cm"},
            "tolerance_cm": 2,
            "usable_h_cm": 135,
            "irregular": False,
            "layers": [{"manual": False, "placements": [{"x": 0, "y": 0, "dx": 40, "dy": 30}]}],
        }
        resp = self.client.post(reverse("ui:planner_carton_new"), {
            "name": "KAR-30x20x40", "width_cm": 30, "height_cm": 20, "length_cm": 40,
            "unit_weight_kg": 0.5, "tare_kg": 0.2, "pieces_per_carton": 1,
            "notes": "", "is_active": "on",
            "layout_design": json.dumps(layout),
        })
        self.assertEqual(resp.status_code, 302)
        c = Carton.objects.get()
        self.assertIsNotNone(c.layout_design)
        self.assertEqual(c.layout_design["tolerance_cm"], 2)
        self.assertEqual(len(c.layout_design["layers"]), 1)

    def test_bad_layout_json_does_not_break_save(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse("ui:planner_carton_new"), {
            "name": "KAR-1", "width_cm": 30, "height_cm": 20, "length_cm": 40,
            "unit_weight_kg": 0.5, "tare_kg": 0.0, "pieces_per_carton": 1,
            "notes": "", "is_active": "on",
            "layout_design": "{not valid json",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Carton.objects.filter(name="KAR-1").exists())


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CartonArtworkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _md_user()
        cls.carton = Carton.objects.create(
            name="KAR-1", length_cm=40, width_cm=30, height_cm=20,
            unit_weight_kg=0.5, pieces_per_carton=1, tare_kg=0.2)

    def setUp(self):
        self.client.force_login(self.user)

    def _upload(self, kind="label", face="front"):
        img = SimpleUploadedFile("a.png", _png_bytes(), content_type="image/png")
        return self.client.post(
            reverse("ui:carton_artwork_upload", args=[self.carton.pk]),
            {"image": img, "face": face, "kind": kind})

    def test_edit_page_shows_artwork_section(self):
        resp = self.client.get(reverse("ui:planner_carton_edit", args=[self.carton.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Wygląd kartonu")

    def test_upload_label_and_print(self):
        r1 = self._upload("label")
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.json()["ok"])
        r2 = self._upload("print")
        self.assertTrue(r2.json()["ok"])
        # one print + one label on the front face
        self.assertEqual(self.carton.artworks.filter(kind="print", face="front").count(), 1)
        self.assertEqual(self.carton.artworks.filter(kind="label", face="front").count(), 1)

    def test_print_replaced_on_reupload(self):
        self._upload("print")
        self._upload("print")
        self.assertEqual(self.carton.artworks.filter(kind="print", face="front").count(), 1)

    def test_reject_non_image(self):
        bad = SimpleUploadedFile("a.txt", b"hello", content_type="text/plain")
        resp = self.client.post(
            reverse("ui:carton_artwork_upload", args=[self.carton.pk]),
            {"image": bad, "face": "front", "kind": "label"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    def test_update_placement(self):
        art_id = self._upload("label").json()["artwork"]["id"]
        resp = self.client.post(
            reverse("ui:carton_artwork_update", args=[art_id]),
            data=json.dumps({"x": 25, "y": 30, "w": 40, "h": 15, "rot": 90}),
            content_type="application/json")
        self.assertTrue(resp.json()["ok"])
        a = CartonArtwork.objects.get(pk=art_id)
        self.assertEqual((a.x_pct, a.y_pct, a.w_pct, a.h_pct, a.rotation_deg), (25, 30, 40, 15, 90))

    def test_list_and_delete(self):
        art_id = self._upload("label").json()["artwork"]["id"]
        lst = self.client.get(reverse("ui:carton_artwork_list", args=[self.carton.pk]))
        self.assertEqual(len(lst.json()["artworks"]), 1)
        d = self.client.post(reverse("ui:carton_artwork_delete", args=[art_id]))
        self.assertTrue(d.json()["ok"])
        self.assertFalse(CartonArtwork.objects.filter(pk=art_id).exists())
