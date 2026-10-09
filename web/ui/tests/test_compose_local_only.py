"""docker-compose.yml to uruchomienie lokalne, nie opis produkcji (audyt SEC-015, BUILD-004)
+ brak przypadkowych pustych plików `file` (DOC-006)."""
from pathlib import Path

import yaml
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[3]


class ComposeLocalOnlyTests(SimpleTestCase):
    def setUp(self):
        self.text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.compose = yaml.safe_load(self.text)

    def test_app_port_published_on_loopback_only(self):
        """SEC-015 / T1: 8000 nie na 0.0.0.0 — obrona w głąb, gdyby firewall przepuścił."""
        for name, svc in self.compose["services"].items():
            for port in svc.get("ports", []):
                with self.subTest(service=name, port=port):
                    self.assertTrue(str(port).startswith("127.0.0.1:"), port)

    def test_header_says_production_does_not_use_it(self):
        """BUILD-004: limity/rotacja logów z compose nie są dowodem zabezpieczenia prod."""
        header = self.text.split("services:", 1)[0]
        self.assertIn("PRODUKCJA NIE używa tego pliku", header)
        self.assertIn("Coolify", header)

    def test_hardening_audit_does_not_cite_compose_as_prod_evidence(self):
        doc = (ROOT / "docs" / "deploy-hardening-audit.md").read_text(encoding="utf-8")
        for row in doc.splitlines():
            if row.startswith(("| 0.2 ", "| 0.3 ")):
                with self.subTest(row=row[:40]):
                    self.assertNotIn("| ✅ |", row)
                    self.assertIn("Coolify", row + doc)
        self.assertIn("| V8 |", doc)


class NoStrayFilesTests(SimpleTestCase):
    def test_no_empty_file_artifacts(self):
        """DOC-006: puste `file` z polecenia powłoki w commicie B-001…B-003."""
        for path in (ROOT / "file", ROOT / "web" / "file"):
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertFalse(path.exists())
