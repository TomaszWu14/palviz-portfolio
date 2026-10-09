import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase

class DataListsRenderTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="boss", password="secret123")
        self.client.post("/login/", {"username": "boss", "password": "secret123"})

    def _ok(self, url, accent, fname):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, url)
        html = r.content.decode()
        self.assertIn("panel-head__chip", html)
        self.assertIn("card--accent", html)
        self.assertIn(accent, html)
        # Portable preview dump (Windows: no /tmp, cp1250 default encoding).
        open(os.path.join(tempfile.gettempdir(), fname), "w", encoding="utf-8").write(html)

    def test_products(self):     self._ok("/planner/products/", "#6366f1", "list_products.html")
    def test_cartons(self):      self._ok("/planner/cartons/", "#2563eb", "list_cartons.html")
    def test_instructions(self): self._ok("/planner/instructions/", "#0891b2", "list_instructions.html")
    def test_locations(self):    self._ok("/magazyn/lokalizacje/typy/", "#0d9488", "list_locations.html")
