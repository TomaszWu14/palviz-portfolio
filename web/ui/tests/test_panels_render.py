import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase

class WarehousePanelsRenderTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="boss", password="secret123")
        self.client.post("/login/", {"username": "boss", "password": "secret123"})

    def _ok(self, url, accent, fname):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, url)
        html = r.content.decode()
        self.assertIn("panel-head__chip", html)
        self.assertIn(accent, html)
        # Portable preview dump (Windows: no /tmp, cp1250 default encoding).
        open(os.path.join(tempfile.gettempdir(), fname), "w", encoding="utf-8").write(html)

    def test_shipments(self):     self._ok("/planner/shipments/", "#7c3aed", "panel_shipments.html")
    def test_model_list(self):    self._ok("/magazyn/model/", "#4f46e5", "panel_model.html")
    def test_heatmap_list(self):  self._ok("/magazyn/heatmapa/", "#ea580c", "panel_heatmap.html")
