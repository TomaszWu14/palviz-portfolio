"""Kontrakt danych 3D: grafiki kartonu (CartonArtwork) trafiają do three_data poziomu
„carton", żeby renderer nałożył je na bryłę. Logika renderera (JS) testowana manualnie."""
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from ui.hierarchy import build_hierarchy
from ui.models import Product, PalletizationInstruction, Carton, CartonArtwork


def _carton(name="K"):
    return Carton.objects.create(name=name, length_cm=40, width_cm=30, height_cm=25,
                                 unit_weight_kg=0.5, pieces_per_carton=10)


def _product(code, carton):
    p = Product.objects.create(code=code, name="X",
                               unit_length_cm=10, unit_width_cm=8, unit_height_cm=5)
    PalletizationInstruction.objects.create(
        product=p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=10,
        carton=carton, carton_l=40, carton_w=30, carton_h=25,
        pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15,
        max_height_total_cm=200)
    return p


def _carton_three(product):
    h = build_hierarchy(product)
    lvl = next(l for l in h["levels"] if l["key"] == "carton")
    return json.loads(lvl["three_data"])


class CartonArtwork3D(TestCase):
    def test_artwork_flows_into_three_data(self):
        c = _carton()
        CartonArtwork.objects.create(
            carton=c, face="front", kind="print", x_pct=5, y_pct=6, w_pct=40, h_pct=30,
            rotation_deg=15, z=1,
            image=SimpleUploadedFile("f.png", b"\x89PNG\r\n", content_type="image/png"))
        td = _carton_three(_product("ART-1", c))
        self.assertIn("artwork", td)
        a = td["artwork"][0]
        self.assertEqual(a["face"], "front")
        self.assertEqual(a["kind"], "print")
        self.assertTrue(a["url"])                       # /media/carton_artwork/…
        self.assertEqual((a["x"], a["w"], a["rot"]), (5, 40, 15))

    def test_no_artwork_no_key(self):
        td = _carton_three(_product("ART-2", _carton("K2")))
        self.assertNotIn("artwork", td)                 # brak grafik → render jak dziś (kolor)

    def test_artwork_flows_into_pallet(self):
        # Paleta też dostaje grafikę kartonu → renderer stawia kartony ze ścianami,
        # nie puste bryły. Wymaga layoutu, żeby w hierarchii pojawił się poziom „pallet".
        c = _carton("KP")
        CartonArtwork.objects.create(
            carton=c, face="front", kind="print", x_pct=0, y_pct=0, w_pct=100, h_pct=100, z=0,
            image=SimpleUploadedFile("p.png", b"\x89PNG\r\n", content_type="image/png"))
        p = _product("ART-3", c)
        instr = p.latest_instruction()
        instr.layouts = [{"name": "L", "layers_used": 1, "cartons_per_pallet": 4,
                          "placements": [{"x": 0, "y": 0, "dx": 40, "dy": 30}]}]
        instr.save()
        h = build_hierarchy(p)
        pal = json.loads(next(l for l in h["levels"] if l["key"] == "pallet")["three_data"])
        self.assertIn("carton_artwork", pal)
        self.assertEqual(pal["carton_artwork"][0]["face"], "front")
