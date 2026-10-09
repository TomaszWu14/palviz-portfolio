import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase

class DashboardRenderTests(TestCase):
    def test_planner_dashboard_renders_grouped_tiles(self):
        get_user_model().objects.create_superuser(username="boss", password="secret123")
        self.client.post("/login/", {"username": "boss", "password": "secret123"})
        r = self.client.get("/planner/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for label in ("Dane podstawowe", "Planowanie i operacje", "Magazyn i analizy"):
            self.assertIn(label, html)
        self.assertIn("--c:#6366f1", html)   # colored tile accent present
        self.assertIn("dash-section", html)
        # Portable preview dump (Windows: no /tmp, cp1250 default encoding).
        open(os.path.join(tempfile.gettempdir(), "dashboard_preview.html"), "w", encoding="utf-8").write(html)
