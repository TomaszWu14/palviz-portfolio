"""Strażnik double-submit (UX audyt top-1) — obecność + składnia JS.

Blok skryptu w scanner/base.html już raz zabił cały ekran jedną brakującą
klamrą (pv-keypad, PR #612) — stąd node --check na blokach <script>.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

BASE = Path(__file__).resolve().parents[2]
APP_JS = BASE / "ui" / "static" / "ui" / "js" / "app.js"
SCANNER_BASE = BASE / "ui" / "templates" / "ui" / "scanner" / "base.html"


def _strip_django(js: str) -> str:
    js = re.sub(r"(['\"])\{\{.*?\}\}\1|(['\"])\{%.*?%\}\2", '"x"', js)
    return re.sub(r"\{\{.*?\}\}|\{%.*?%\}", '"x"', js)


class SubmitGuardTests(SimpleTestCase):
    def test_guard_present_in_both_layouts(self):
        for path in (APP_JS, SCANNER_BASE):
            text = path.read_text(encoding="utf-8")
            self.assertIn("data-gv-submitting", text, path.name)
            self.assertIn("data-no-guard", text, path.name)
            self.assertIn("pageshow", text, path.name)

    def test_upload_feedback_pattern(self):
        """Top-2 audytu: uploady idą przez gvUpload/pvUpload, nie gołe .submit()."""
        self.assertIn("window.gvUpload", APP_JS.read_text(encoding="utf-8"))
        self.assertIn("window.pvUpload", SCANNER_BASE.read_text(encoding="utf-8"))
        stray = []
        for tpl in BASE.rglob("templates/**/*.html"):
            text = tpl.read_text(encoding="utf-8")
            if "type=\"file\"" in text and re.search(
                r'onchange="[^"]*\.submit\(\)"', text
            ):
                stray.append(str(tpl))
        self.assertEqual(stray, [], "upload bez feedbacku (użyj gvUpload/pvUpload)")

    def test_high_impact_actions_have_confirm(self):
        """Top-3 audytu: akcje o dużym skutku mają confirm()."""
        tpl = BASE  # katalog web/
        spots = {
            tpl / "ui/templates/ui/admin/users.html": "Dezaktywować użytkownika",
            tpl / "transport/templates/ui/driver_confirm.html": "NIE odbierzesz",
            tpl / "huctl/templates/ui/scanner/investigation_detail.html": "Potwierdzić błąd",
            tpl / "ui/templates/ui/control/leader.html": "Zdjąć przydział",
            tpl / "ui/templates/ui/control/hub.html": "wybór typów magazynu",
        }
        for path, needle in spots.items():
            self.assertIn(needle, path.read_text(encoding="utf-8"), path.name)

    def test_no_silent_fetch_catches(self):
        """Top-4 audytu: naprawione pliki nie wracają do pustego .catch()."""
        fixed = [
            BASE / "ui/templates/ui/zaria/conversation.html",
            BASE / "ui/templates/ui/carton_opt/redesign_detail.html",
            BASE / "ui/templates/ui/_messages_bell.html",
            BASE / "ui/templates/ui/_notif_alert.html",
            BASE / "ui/templates/ui/scanner/base.html",
        ]
        empty = re.compile(r"\.catch\((function\(\)|\(\)\s*=>)\s*\{\s*\}\)")
        for path in fixed:
            self.assertIsNone(empty.search(path.read_text(encoding="utf-8")), path.name)

    def test_scanner_scripts_and_app_js_parse(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node niedostępny")
        blocks = [APP_JS.read_text(encoding="utf-8")]
        html = SCANNER_BASE.read_text(encoding="utf-8")
        blocks += [_strip_django(b) for b in re.findall(r"<script>(.*?)</script>", html, re.S)]
        for i, js in enumerate(blocks):
            with tempfile.NamedTemporaryFile(
                "w", suffix=".js", delete=False, encoding="utf-8"
            ) as f:
                f.write(js)
            r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
            Path(f.name).unlink()
            self.assertEqual(r.returncode, 0, f"blok {i}: {r.stderr[:300]}")
