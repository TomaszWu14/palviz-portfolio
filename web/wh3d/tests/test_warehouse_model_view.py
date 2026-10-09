"""Regression: warehouse model 3D view used a non-existent `get_item` filter → 500."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from ui.models import WarehouseModel, WarehouseModelRack


class WarehouseModelViewTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})

    def test_view_renders_with_racks(self):
        wm = WarehouseModel.objects.create(name="M")
        WarehouseModelRack.objects.create(model=wm, zone="B0", rack_id="01")
        WarehouseModelRack.objects.create(model=wm, zone="C0", rack_id="02")
        r = self.client.get(f"/magazyn/model/{wm.pk}/view/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Strefa B0")

    def test_view_renders_without_racks(self):
        wm = WarehouseModel.objects.create(name="Empty")
        self.assertEqual(self.client.get(f"/magazyn/model/{wm.pk}/view/").status_code, 200)

    def test_view_has_3d_boot_feedback(self):
        """UX #5: widok 3D nie może cicho paść czarnym ekranem — spinner +
        guard WebGL + wh3dReady muszą być obecne."""
        wm = WarehouseModel.objects.create(name="M")
        r = self.client.get(f"/magazyn/model/{wm.pk}/view/")
        self.assertContains(r, "wh3d-status")
        self.assertContains(r, "__wh3dNoWebGL")
        self.assertContains(r, "wh3dReady()")


class StoredXSSGuardTests(TestCase):
    """Regresja bezpieczeństwa: wolny tekst pól regału/elementu (zone/rack_id/label)
    trafia do <script> przez |safe. Musi iść przez safe_json (escape </>&), nie
    zwykły json.dumps — inaczej '<script>' w polu wychodzi z tagu (stored XSS)."""

    def setUp(self):
        get_user_model().objects.create_superuser(username="x", password="x")
        self.client.post("/login/", {"username": "x", "password": "x"})

    def test_rack_zone_payload_is_escaped_in_script(self):
        from django.urls import reverse
        wm = WarehouseModel.objects.create(name="XSS")
        # 14 znaków — mieści się w zone (max_length=20)
        WarehouseModelRack.objects.create(model=wm, zone="<script>PWNXSS", rack_id="01")
        r = self.client.get(reverse("ui:warehouse_model_view", args=[wm.pk]))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        # Surowy wstrzyknięty tag NIE może się pojawić…
        self.assertNotIn("<script>PWNXSS", body)
        # …a dane i tak są wyrenderowane, tyle że zescapowane (transparentne dla JSON.parse).
        self.assertIn("\\u003cscript\\u003ePWNXSS", body)


class ViewFloatLocalizationTests(TestCase):
    """Regresja: FLOOR_W/FLOOR_D w JS muszą mieć KROPKĘ dziesiętną, nie polski przecinek
    ({% localize off %}) — inaczej 'const FLOOR_W = 40,0;' wywala cały moduł 3D."""
    def setUp(self):
        from django.contrib.auth import get_user_model
        get_user_model().objects.create_superuser(username="lf", password="x")
        self.client.post("/login/", {"username": "lf", "password": "x"})

    def test_floor_consts_use_dot(self):
        from django.urls import reverse
        wm = WarehouseModel.objects.create(name="LF", floor_width_m=40, floor_depth_m=20)
        r = self.client.get(reverse("ui:warehouse_model_view", args=[wm.pk]))
        self.assertContains(r, "const FLOOR_W = 40.0;")
        self.assertNotContains(r, "40,0;")


class InstancingGuardTests(TestCase):
    """R1: stal regałów renderowana przez InstancedMesh (3 draw calle), nie per-mesh.
    Strażnik regresji mesh-explosion (regał 20×5 ≈ 1400 draw calls przed fixem)."""

    def test_view_template_uses_instancing(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from django.urls import reverse
        from ui.models import WarehouseModel, WarehouseModelRack
        from ui.roles import GROUP_MASTER_DATA
        u = get_user_model().objects.create_user(username="inst", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)
        wm = WarehouseModel.objects.create(name="M1")
        WarehouseModelRack.objects.create(model=wm, zone="A", rack_id="1", n_bays=4,
                                          n_levels=3, bay_width_cm=200, depth_cm=110,
                                          level_height_cm=200, x_m=0, y_m=0)
        r = self.client.get(reverse("ui:warehouse_model_view", args=[wm.pk]))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("InstancedMesh", body)
        self.assertIn("_steelBatches", body)
        # Stara ścieżka (Mesh per element stali w put()) nie może wrócić —
        # marker unikalny dla starego put(); Meshe hall-features (kilka szt.) są OK.
        self.assertNotIn("group.add(m); return m;", body)
