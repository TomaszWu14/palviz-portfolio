"""Faza 2 grafik 3D: sztuka (Product) + OPZ (InnerPack). Kontrakt three_data + endpointy
uploadu (te same generyczne helpery co karton)."""
import io
import json
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.hierarchy import build_hierarchy
from ui.models import (Product, PalletizationInstruction, Carton, InnerPack,
                       ProductArtwork, InnerPackArtwork)
from ui.roles import GROUP_MASTER_DATA


def _png():
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (4, 4), (200, 120, 60)).save(b, "PNG"); return b.getvalue()


def _upload_file():
    return SimpleUploadedFile("a.png", _png(), content_type="image/png")


def _md_user():
    u = get_user_model().objects.create_user("md", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
    return u


def _full_product(code="P1"):
    ip = InnerPack.objects.create(name="OPZ", length_cm=30, width_cm=20, height_cm=15,
                                  units_per_pack=6, tare_kg=0.1)
    c = Carton.objects.create(name="K", length_cm=40, width_cm=30, height_cm=25,
                              unit_weight_kg=0.5, pieces_per_carton=12,
                              inner_pack=ip, packs_per_carton=2)
    p = Product.objects.create(code=code, name="X",
                               unit_length_cm=10, unit_width_cm=8, unit_height_cm=5)
    PalletizationInstruction.objects.create(
        product=p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=12,
        carton=c, inner_pack=ip, packs_per_carton=2, pcs_per_inner_pack=6,
        carton_l=40, carton_w=30, carton_h=25,
        pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15, max_height_total_cm=200)
    return p, ip


def _level(product, key):
    h = build_hierarchy(product)
    lvl = next(l for l in h["levels"] if l["key"] == key)
    return json.loads(lvl["three_data"])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ArtworkContract(TestCase):
    def test_unit_and_opz_artwork_in_three_data(self):
        p, ip = _full_product()
        ProductArtwork.objects.create(product=p, face="front", kind="print",
                                      image=_upload_file())
        InnerPackArtwork.objects.create(inner_pack=ip, face="back", kind="label",
                                        x_pct=10, y_pct=10, w_pct=40, h_pct=30,
                                        image=_upload_file())
        unit_td = _level(p, "unit")
        opz_td = _level(p, "inner_pack")
        self.assertEqual(unit_td["artwork"][0]["face"], "front")
        self.assertTrue(unit_td["artwork"][0]["url"])
        self.assertEqual(opz_td["artwork"][0]["face"], "back")

    def test_no_artwork_no_key(self):
        p, _ = _full_product("P2")
        self.assertNotIn("artwork", _level(p, "unit"))
        self.assertNotIn("artwork", _level(p, "inner_pack"))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ArtworkUploadEndpoints(TestCase):
    def setUp(self):
        self.client.force_login(_md_user())

    def test_product_upload_creates_artwork(self):
        p, _ = _full_product("P3")
        r = self.client.post(reverse("ui:product_artwork_upload", args=[p.pk]),
                             {"image": _upload_file(), "face": "front", "kind": "print"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(ProductArtwork.objects.filter(product=p, face="front", kind="print").count(), 1)

    def test_inner_pack_upload_creates_artwork(self):
        _, ip = _full_product("P4")
        r = self.client.post(reverse("ui:inner_pack_artwork_upload", args=[ip.pk]),
                             {"image": _upload_file(), "face": "left", "kind": "label"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(InnerPackArtwork.objects.filter(inner_pack=ip).count(), 1)

    def test_upload_rejects_missing_file(self):
        p, _ = _full_product("P5")
        r = self.client.post(reverse("ui:product_artwork_upload", args=[p.pk]), {"face": "front"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()["ok"])

    def test_product_glb_upload(self):
        # .glb z macierzy grafik → slot Product.glb_model (nie grafika-nadruk).
        from django.core.files.uploadedfile import SimpleUploadedFile
        p, _ = _full_product("P7")
        r = self.client.post(reverse("ui:product_glb_upload", args=[p.pk]),
                             {"glb": SimpleUploadedFile("op.glb", b"glTF\x02\x00")})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        p.refresh_from_db()
        self.assertTrue(p.glb_model.name.endswith(".glb"))

    def test_ju_upload_routes_by_extension(self):
        # Jeden endpoint mediów JU: .glb → ju_glb_model, .png → ju_image.
        from django.core.files.uploadedfile import SimpleUploadedFile
        p, _ = _full_product("P9")
        r = self.client.post(reverse("ui:product_ju_upload", args=[p.pk]),
                             {"file": SimpleUploadedFile("ju.glb", b"glTF\x02\x00")})
        self.assertTrue(r.json()["ok"])
        r = self.client.post(reverse("ui:product_ju_upload", args=[p.pk]),
                             {"file": SimpleUploadedFile("ju.png", b"\x89PNG")})
        self.assertTrue(r.json()["ok"])
        p.refresh_from_db()
        self.assertTrue(p.ju_glb_model.name.endswith(".glb"))
        self.assertTrue(p.ju_image.name.endswith(".png"))

    def test_glb_upload_rejects_wrong_extension(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        p, _ = _full_product("P8")
        r = self.client.post(reverse("ui:product_glb_upload", args=[p.pk]),
                             {"glb": SimpleUploadedFile("x.png", b"\x89PNG")})
        self.assertEqual(r.status_code, 400)

    def test_list_and_delete(self):
        _, ip = _full_product("P6")
        a = InnerPackArtwork.objects.create(inner_pack=ip, face="top", kind="label",
                                            image=_upload_file())
        lst = self.client.get(reverse("ui:inner_pack_artwork_list", args=[ip.pk])).json()
        self.assertEqual(len(lst["artworks"]), 1)
        d = self.client.post(reverse("ui:inner_pack_artwork_delete", args=[a.pk]))
        self.assertTrue(d.json()["ok"])
        self.assertEqual(InnerPackArtwork.objects.count(), 0)
