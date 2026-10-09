"""STRUCT-01: strażnik granicy między aplikacjami-liśćmi (wh3d/huctl/transport).

Żadna aplikacja-liść nie importuje siostrzanej aplikacji-liścia — każda może
importować tylko wspólny rdzeń `ui` (patrz `test_wh3d_boundary.py`, który pina
seam ui<->wh3d). Pęknięcie tego testu = sprzężenie sioster, którego architektura
świadomie unika (PR #545-#549 rozdzieliły te aplikacje właśnie po to)."""
import ast
import pathlib
import re

from django.test import SimpleTestCase

_ROOT = pathlib.Path(__file__).resolve().parents[3]  # repo root
_WEB = _ROOT / "web"

_LEAF_APPS = ("wh3d", "huctl", "transport")


class ModuleBoundaryTests(SimpleTestCase):
    def test_no_leaf_app_imports_a_sibling_leaf_app(self):
        offenders = []
        for app in _LEAF_APPS:
            siblings = [a for a in _LEAF_APPS if a != app]
            forbidden = re.compile(
                r"^(from|import) (" + "|".join(siblings) + r")(\.|$| )"
            )
            app_dir = _WEB / app
            for f in app_dir.rglob("*.py"):
                for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    ls = line.strip()
                    if forbidden.match(ls):
                        offenders.append(f"{app}/{f.relative_to(app_dir)}:{n}: {ls}")
        self.assertEqual(offenders, [])

    def test_ui_does_not_import_leaf_app_views(self):
        """ARCH-001: rdzeń `ui` nie sięga do modułów WIDOKÓW liści (huctl/transport/wh3d
        .views) — także importami leniwymi wewnątrz funkcji. Logikę współdzieloną liść
        wystawia w module serwisowym (np. huctl.kpi, huctl.hu_import, transport.sms,
        transport.kpi, wh3d.locations), a `ui` importuje właśnie ją."""
        forbidden = tuple(f"{a}.views" for a in _LEAF_APPS)
        ui_dir = _WEB / "ui"
        offenders = []
        for f in ui_dir.rglob("*.py"):
            if "tests" in f.relative_to(ui_dir).parts:
                continue
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                else:
                    continue
                for mod in names:
                    if mod in forbidden or mod.startswith(tuple(x + "." for x in forbidden)):
                        offenders.append(f"ui/{f.relative_to(ui_dir).as_posix()}:{node.lineno}: {mod}")
        self.assertEqual(offenders, [])
