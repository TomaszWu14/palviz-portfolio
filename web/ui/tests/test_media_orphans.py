"""Komenda media_orphans (DATA-002): plik powiązany zostaje, osierocony jest
wykrywany i usuwany tylko z --delete, świeży (<24 h) jest pomijany."""
import os
import tempfile
import time
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings

from ui.models import SiteInfo


class MediaOrphansTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        old = time.time() - 48 * 3600
        (root / "site").mkdir()
        (root / "ewm_tasks").mkdir()
        self.linked = root / "site" / "mapa.png"
        self.orphan = root / "site" / "stara.png"
        self.fresh = root / "site" / "nowa.png"
        self.temp = root / "ewm_tasks" / ("a" * 32 + ".csv")
        for p in (self.linked, self.orphan, self.fresh, self.temp):
            p.write_bytes(b"x" * 10)
        for p in (self.linked, self.orphan, self.temp):
            os.utime(p, (old, old))
        SiteInfo.objects.create(site_map="site/mapa.png")
        self.override = override_settings(MEDIA_ROOT=self.tmp.name)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def _run(self, *args):
        out = StringIO()
        call_command("media_orphans", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_only_old_orphan(self):
        out = self._run()
        self.assertIn("site/stara.png", out)
        self.assertIn("Osierocone pliki: 1", out)
        self.assertNotIn("mapa.png", out)
        self.assertNotIn("nowa.png", out)
        self.assertNotIn("ewm_tasks", out)
        self.assertTrue(self.orphan.exists())   # dry-run nic nie kasuje

    def test_delete_removes_only_orphan(self):
        self._run("--delete")
        self.assertFalse(self.orphan.exists())
        self.assertTrue(self.linked.exists())
        self.assertTrue(self.fresh.exists())
        self.assertTrue(self.temp.exists())
