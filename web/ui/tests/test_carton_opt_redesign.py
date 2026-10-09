"""Projekty A/B (carton_opt): snapshot A zamrożony, metryki, uprawnienia, blokada po akceptacji."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import PackagingRedesign, PalletizationInstruction, Product
from ui.roles import GROUP_OPTIMIZER, GROUP_VIEWER


def _product_with_instr(code="RD-1"):
    p = Product.objects.create(code=code, name="X", unit_length_cm=10,
                               unit_width_cm=8, unit_height_cm=5)
    PalletizationInstruction.objects.create(
        product=p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=24,
        units_per_piece=1, carton_l=40, carton_w=30, carton_h=25,
        pallet_code="EU", pallet_length_cm=120, pallet_width_cm=80,
        pallet_base_height_cm=15, max_height_total_cm=200, demand_pcs=1000)
    return p


class SnapshotTests(TestCase):
    def test_snapshot_frozen_against_master_data_change(self):
        p = _product_with_instr()
        rd = PackagingRedesign.create_for(p, scope="oba", user=None)
        self.assertEqual(rd.a["carton_l"], 40)
        self.assertEqual(rd.a["unit_l"], 10)
        self.assertEqual(rd.a["pcs_per_carton"], 24)
        # Zmiana master daty PO utworzeniu nie zmienia A.
        instr = p.latest_instruction()
        instr.carton_l = 99
        instr.save()
        p.unit_length_cm = 77
        p.save()
        rd.refresh_from_db()
        self.assertEqual(rd.a["carton_l"], 40)
        self.assertEqual(rd.a["unit_l"], 10)


class MetricsTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="opt", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_OPTIMIZER)[0])
        self.client.force_login(u)
        self.p = _product_with_instr("RD-M")
        self.rd = PackagingRedesign.objects.filter(product=self.p).first() \
            or PackagingRedesign.create_for(self.p, scope="karton", user=None)
        self.rd.annual_volume_pcs = 100_000
        self.rd.save()

    def test_metrics_endpoint_b_better(self):
        # B: niższy karton (40×30×20, 24 szt) → więcej warstw → więcej kartonów/paletę.
        r = self.client.get(reverse("ui:carton_opt_redesign_metrics"),
                            {"pk": self.rd.pk, "b_carton_l": 40, "b_carton_w": 30,
                             "b_carton_h": 20, "b_pcs_per_carton": 24})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertGreater(d["b"]["cartons_per_pallet"], d["a"]["cartons_per_pallet"])
        self.assertGreater(d["a"]["pallets_per_year"], d["b"]["pallets_per_year"])
        self.assertAlmostEqual(d["a"]["carton_m3"], 0.03, places=3)   # 40×30×25 cm
        self.assertIn("fill", d["delta"])

    def test_metrics_endpoint_annual_override(self):
        r = self.client.get(reverse("ui:carton_opt_redesign_metrics"),
                            {"pk": self.rd.pk, "b_carton_l": 40, "b_carton_w": 30,
                             "b_carton_h": 20, "b_pcs_per_carton": 24,
                             "annual_volume_pcs": 200_000})
        d = r.json()
        # Nadpisany wolumen (200k zamiast 100k z rekordu) → ~2× palet rocznie.
        self.assertGreater(d["b"]["pallets_per_year"], 0)
        base = self.client.get(reverse("ui:carton_opt_redesign_metrics"),
                               {"pk": self.rd.pk, "b_carton_l": 40, "b_carton_w": 30,
                                "b_carton_h": 20, "b_pcs_per_carton": 24}).json()
        self.assertGreater(d["b"]["pallets_per_year"], base["b"]["pallets_per_year"])


class ViewTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.opt = U.objects.create_user(username="opt2", password="x")
        self.opt.groups.add(Group.objects.get_or_create(name=GROUP_OPTIMIZER)[0])
        self.viewer = U.objects.create_user(username="view", password="x")
        self.viewer.groups.add(Group.objects.get_or_create(name=GROUP_VIEWER)[0])
        self.p = _product_with_instr("RD-V")
        self.client.force_login(self.opt)

    def _create(self):
        self.client.post(reverse("ui:carton_opt_redesign_new"),
                         {"ref_code": "RD-V", "scope": "karton"})
        return PackagingRedesign.objects.get(product=self.p)

    def test_create_save_accept_locks_b(self):
        rd = self._create()
        self.assertEqual(rd.a["carton_l"], 40)              # snapshot przy tworzeniu
        url = reverse("ui:carton_opt_redesign_save", args=[rd.pk])
        self.client.post(url, {"action": "save", "b_carton_l": 40, "b_carton_w": 30,
                               "b_carton_h": 20, "b_pcs_per_carton": 24})
        rd.refresh_from_db()
        self.assertEqual(rd.b_carton_h, 20)
        self.client.post(url, {"action": "accept"})
        rd.refresh_from_db()
        self.assertEqual(rd.status, "accepted")
        # Po akceptacji edycja B odbita.
        self.client.post(url, {"action": "save", "b_carton_h": 99})
        rd.refresh_from_db()
        self.assertEqual(rd.b_carton_h, 20)

    def test_one_project_per_index(self):
        # Jeden indeks = jedna linia. Drugie „Nowy projekt" na ten sam REF NIE tworzy
        # duplikatu — otwiera istniejący (koniec bugu z 5× tym samym indeksem).
        rd = self._create()
        r = self.client.post(reverse("ui:carton_opt_redesign_new"),
                             {"ref_code": "RD-V", "scope": "oba"})
        self.assertRedirects(r, reverse("ui:carton_opt_redesign_detail", args=[rd.pk]))
        self.assertEqual(PackagingRedesign.objects.filter(product=self.p).count(), 1)

    def test_viewer_cannot_write(self):
        rd = self._create()
        self.client.force_login(self.viewer)
        r = self.client.post(reverse("ui:carton_opt_redesign_save", args=[rd.pk]),
                             {"action": "save", "b_carton_h": 10})
        self.assertIn(r.status_code, (302, 403))
        rd.refresh_from_db()
        self.assertIsNone(rd.b_carton_h)

    def test_render_upload_validates_extension(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        rd = self._create()
        url = reverse("ui:carton_opt_redesign_save", args=[rd.pk])
        self.client.post(url, {"action": "save",
                               "a_render": SimpleUploadedFile("x.exe", b"MZ")})
        rd.refresh_from_db()
        self.assertFalse(rd.a_render)
        self.client.post(url, {"action": "save",
                               "a_render": SimpleUploadedFile("a.glb", b"glTF\x02\x00")})
        rd.refresh_from_db()
        self.assertTrue(rd.a_render.name.endswith(".glb"))


class TemplateSmokeTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="opt3", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_OPTIMIZER)[0])
        self.client.force_login(u)
        self.p = _product_with_instr("RD-T")
        self.rd = PackagingRedesign.create_for(self.p, scope="oba", user=u)

    def test_list_and_detail_render(self):
        r = self.client.get(reverse("ui:carton_opt_redesigns"))
        self.assertContains(r, "RD-T")
        r = self.client.get(reverse("ui:carton_opt_redesign_detail", args=[self.rd.pk]))
        self.assertContains(r, "WERSJA OBECNA")
        self.assertContains(r, "canvas-a")
        self.assertContains(r, "canvas-b")
        self.assertNotContains(r, "{# ")          # guard: komentarze jednolinijkowe

    def test_list_warns_when_truncated(self):
        """Powyżej limitu operator dostaje jawne ostrzeżenie, nie ciche obcięcie."""
        from unittest.mock import patch
        # Bez przekroczenia — brak ostrzeżenia.
        r = self.client.get(reverse("ui:carton_opt_redesigns"))
        self.assertFalse(r.context["truncated"])
        self.assertNotContains(r, "Pokazano")
        # Limit obniżony do 0 → 1 projekt > limit → ostrzeżenie z liczbami.
        # Po splicie (UI-V1) widok czyta stałą z carton_opt_redesign, nie z agregatora.
        with patch("ui.views.carton_opt_redesign.REDESIGNS_LIST_LIMIT", 0):
            r = self.client.get(reverse("ui:carton_opt_redesigns"))
        self.assertTrue(r.context["truncated"])
        self.assertEqual(r.context["total"], 1)
        self.assertContains(r, "Pokazano 0 z 1")
