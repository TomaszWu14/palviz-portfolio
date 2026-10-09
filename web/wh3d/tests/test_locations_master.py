"""Master-data location browser + exports (django-tables2 / django-filter / django-htmx /
XlsxWriter / WeasyPrint) and the django-ninja REST API."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.models import WarehouseLocationMaster, WarehouseLocationMasterBatch
from ui.roles import GROUP_MASTER_DATA


def _md_login(client):
    u = get_user_model().objects.create_user(username="md", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
    client.force_login(u)
    return u


def _seed_batch():
    batch = WarehouseLocationMasterBatch.objects.create(name="B", is_active=True)
    WarehouseLocationMaster.objects.create(batch=batch, location_code="B0-01-300A", level=1,
                                           warehouse_type="0050", width_mm=800, depth_mm=1100,
                                           height_mm=1800, max_volume_m3=1.58, max_weight_kg=1000)
    WarehouseLocationMaster.objects.create(batch=batch, location_code="B0-01-300C", level=3,
                                           warehouse_type="", width_mm=400, depth_mm=1100,
                                           height_mm=1000, max_volume_m3=0.44, max_weight_kg=500)
    batch.location_count = 2
    batch.save()
    return batch


class LocationMasterBrowseTests(TestCase):
    def setUp(self):
        _md_login(self.client)
        _seed_batch()

    def test_browse_renders_full_page(self):
        resp = self.client.get(reverse("ui:location_master_browse"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Master data lokalizacji")
        self.assertContains(resp, "B0-01-300A")
        # numpy/polars stats panel: 2 locations, 1 half-slot.
        self.assertContains(resp, "Statystyki")
        self.assertEqual(resp.context["stats"]["count"], 2)
        self.assertEqual(resp.context["stats"]["half_slots"], 1)

    def test_htmx_returns_table_partial_only(self):
        resp = self.client.get(reverse("ui:location_master_browse"), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("lm-table", body)
        self.assertNotIn("<title>", body)   # no full page chrome

    def test_filter_by_width_keeps_only_half_slots(self):
        resp = self.client.get(reverse("ui:location_master_browse"), {"max_width_mm": "500"})
        self.assertContains(resp, "B0-01-300C")
        self.assertNotContains(resp, "B0-01-300A")

    def test_xlsx_export(self):
        resp = self.client.get(reverse("ui:location_master_export_xlsx"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])
        self.assertTrue(resp.content[:2] == b"PK")   # xlsx is a zip

    def test_pdf_export_returns_document(self):
        # WeasyPrint needs native libs; the view degrades to HTML if absent — either is 200.
        resp = self.client.get(reverse("ui:location_master_export_pdf"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(resp["Content-Type"].split(";")[0], ("application/pdf", "text/html"))

    def test_requires_master_data_role(self):
        self.client.logout()
        viewer = get_user_model().objects.create_user(username="v", password="x")
        self.client.force_login(viewer)
        resp = self.client.get(reverse("ui:location_master_browse"))
        self.assertIn(resp.status_code, (302, 403))


@override_settings(PALVIZ_API_TOKEN="secret-token")
class NinjaApiTests(TestCase):
    def setUp(self):
        _seed_batch()

    def test_health_requires_token(self):
        self.assertEqual(self.client.get("/api/v2/health").status_code, 401)

    def test_health_with_token(self):
        resp = self.client.get("/api/v2/health", HTTP_X_API_KEY="secret-token")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        self.assertEqual(resp.json()["locations"], 2)

    def test_get_location(self):
        resp = self.client.get("/api/v2/locations/B0-01-300C", HTTP_X_API_KEY="secret-token")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["width_mm"], 400)

    def test_get_location_404(self):
        resp = self.client.get("/api/v2/locations/NOPE", HTTP_X_API_KEY="secret-token")
        self.assertEqual(resp.status_code, 404)

    def test_list_returns_bare_list(self):
        # Contract the external scanner depends on: a JSON array, not a wrapped object.
        resp = self.client.get("/api/v2/locations", HTTP_X_API_KEY="secret-token")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)
        self.assertEqual(len(resp.json()), 2)

    def test_list_offset_paging(self):
        page1 = self.client.get("/api/v2/locations?limit=1&offset=0",
                                HTTP_X_API_KEY="secret-token").json()
        page2 = self.client.get("/api/v2/locations?limit=1&offset=1",
                                HTTP_X_API_KEY="secret-token").json()
        self.assertEqual(len(page1), 1)
        self.assertEqual(len(page2), 1)
        self.assertNotEqual(page1[0]["location_code"], page2[0]["location_code"])
