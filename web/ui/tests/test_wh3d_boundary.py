"""W1: strażnik granicy aplikacji wh3d (Magazyn 3D).

wh3d może importować z ui WYŁĄCZNIE wspólne jądro (ui.views.core, ui.models,
ui.roles) — nie moduły feature'owe. Reszta ui nie importuje modułów wh3d poza
jawnym seamem (wh3d.locations.active_master_qs dla karty lokalizacji PHV). Pęknięcie tego
testu = wciekanie sprzężeń, które zablokuje przyszłe pełne wydzielenie (W2+)."""
import pathlib
import re

from django.test import SimpleTestCase

_ROOT = pathlib.Path(__file__).resolve().parents[3]   # repo root
_WH3D = _ROOT / "web" / "wh3d"
_UI = _ROOT / "web" / "ui"


class Wh3dBoundaryTests(SimpleTestCase):
    def test_wh3d_imports_only_shared_kernel_from_ui(self):
        allowed = re.compile(r"from ui\.(views\.core|models|roles|views import|"
                             r"notifications|theme|hierarchy|tables|filters|forms)")
        offenders = []
        for f in _WH3D.rglob("*.py"):
            if "tests" in f.relative_to(_WH3D).parts:
                # Test fixtures live in wh3d/tests/ (Phase 6, TEST-01) but aren't
                # application code — the boundary this guard pins is production
                # coupling, not test-fixture imports of shared ui test helpers.
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip().startswith("from ui.") and not allowed.search(line):
                    offenders.append(f"{f.name}:{n}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_ui_features_do_not_import_wh3d_beyond_seam(self):
        # Jedyny dozwolony seam: karta lokalizacji PHV czyta aktywną master datę.
        seam = {"phv_data.py": "from wh3d.locations import active_master_qs"}
        offenders = []
        for f in (_UI / "views").glob("*.py"):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                ls = line.strip()
                if ls.startswith(("from wh3d", "import wh3d")) and ls != seam.get(f.name):
                    if f.name == "__init__.py":     # re-eksport URL-owy jest dozwolony
                        continue
                    offenders.append(f"{f.name}:{n}: {ls}")
        self.assertEqual(offenders, [])
