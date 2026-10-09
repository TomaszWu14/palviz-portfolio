"""Model 3D (glb) kartonu trafia do three_data palety — renderer klonuje go na pozycje
(inaczej karton z glb dawał szary stos). Logika JS testowana manualnie."""
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from ui.hierarchy import build_hierarchy
from ui.models import Product, PalletizationInstruction, Carton


def _pallet_three(product):
    h = build_hierarchy(product)
    lvl = next(l for l in h["levels"] if l["key"] == "pallet")
    return json.loads(lvl["three_data"])


def _product(code, carton):
    p = Product.objects.create(code=code, name="X",
                               unit_length_cm=10, unit_width_cm=8, unit_height_cm=5)
    instr = PalletizationInstruction.objects.create(
        product=p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=10,
        carton=carton, carton_l=40, carton_w=30, carton_h=25,
        pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15,
        max_height_total_cm=200)
    instr.layouts = [{"name": "L", "layers_used": 2, "cartons_per_pallet": 4,
                      "placements": [{"x": 0, "y": 0, "dx": 40, "dy": 30}]}]
    instr.save()
    return p


class PalletGlbTests(TestCase):
    def test_glb_url_flows_into_pallet(self):
        c = Carton.objects.create(name="K", length_cm=40, width_cm=30, height_cm=25,
                                  unit_weight_kg=0.5, pieces_per_carton=10,
                                  glb_model=SimpleUploadedFile("m.glb", b"glTF\x02\x00",
                                                               content_type="model/gltf-binary"))
        td = _pallet_three(_product("GLB-1", c))
        self.assertIn("carton_glb_url", td)
        self.assertTrue(td["carton_glb_url"].endswith(".glb"))

    def test_no_glb_no_key(self):
        c = Carton.objects.create(name="K2", length_cm=40, width_cm=30, height_cm=25,
                                  unit_weight_kg=0.5, pieces_per_carton=10)
        td = _pallet_three(_product("GLB-2", c))
        self.assertNotIn("carton_glb_url", td)

    def test_product_glb_flows_into_unit_level(self):
        # Model .glb wgrany na produkcie → glb_url na poziomie OP/sztuka ("unit").
        c = Carton.objects.create(name="K3", length_cm=40, width_cm=30, height_cm=25,
                                  unit_weight_kg=0.5, pieces_per_carton=10)
        p = _product("GLB-3", c)
        p.glb_model = SimpleUploadedFile("op.glb", b"glTF\x02\x00",
                                         content_type="model/gltf-binary")
        p.save()
        lvl = next(l for l in build_hierarchy(p)["levels"] if l["key"] == "unit")
        td = json.loads(lvl["three_data"])
        self.assertTrue(td.get("glb_url", "").endswith(".glb"))
