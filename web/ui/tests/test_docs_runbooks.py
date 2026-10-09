"""Strażnik dokumentacji operacyjnej (audyt 2026-09: DOC-003, DOC-004, BACKUP-002).

Odnośniki `docs/…` w skryptach operacyjnych muszą wskazywać istniejące pliki, a runbook
odtworzenia musi zawierać wszystkie scenariusze awarii — inaczej procedura „gnije” po cichu
(backup.sh przez miesiące odsyłał do nieistniejącej sekcji ARCHITECTURE.md)."""
import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[3]


class DocsRunbooksTests(SimpleTestCase):
    def test_script_doc_links_exist(self):
        missing = []
        for script in (ROOT / "scripts").glob("*.sh"):
            for ref in re.findall(r"docs/[\w./-]+\.md", script.read_text(encoding="utf-8")):
                if not (ROOT / ref).is_file():
                    missing.append(f"{script.name} → {ref}")
        self.assertEqual(missing, [])

    def test_backup_script_points_to_restore_runbook(self):
        self.assertIn("docs/runbook-odtworzenie.md", (ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8"))

    def test_restore_runbook_covers_all_scenarios(self):
        text = (ROOT / "docs" / "runbook-odtworzenie.md").read_text(encoding="utf-8")
        for scenario in "ABCDE":
            self.assertIn(f"## Scenariusz {scenario}", text)
        self.assertIn("Test odtworzenia", text)

    def test_handover_docs_tracked_and_indexed(self):
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        for name in ("runbook-operatora.md", "runbook-odtworzenie.md"):
            self.assertTrue((ROOT / "docs" / name).is_file(), name)
            self.assertIn(name, index)


class DocsDriftTests(SimpleTestCase):
    """CLAUDE.md/README to instrukcja dla ludzi i asystentów — rozjazd z kodem (audyt DOC-001/002:
    polecenie testów uruchamiało 133 z 286 modułów, „seven role groups” przy 9 grupach)."""

    SUITES = ("ui.tests", "wh3d.tests", "huctl.tests", "transport.tests")

    def test_documented_test_command_runs_all_app_suites(self):
        for doc in ("CLAUDE.md", "README.md"):
            text = (ROOT / doc).read_text(encoding="utf-8")
            cmds = [line for line in text.splitlines() if "manage.py test " in line]
            self.assertTrue(cmds, doc)
            for line in cmds:
                for suite in self.SUITES:
                    self.assertIn(suite, line, f"{doc}: {line.strip()}")

    def test_role_count_matches_contract(self):
        from core.roles import ALL_GROUPS
        words = {7: "seven", 8: "eight", 9: "nine", 10: "ten"}
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(f"seed the {words[len(ALL_GROUPS)]} role groups", text)
        self.assertNotRegex(text, r"seed the (?!%s)\w+ role groups" % words[len(ALL_GROUPS)])


class AdrRegisterTests(SimpleTestCase):
    """Rejestr ADR (audyt DOC-007): każda decyzja ma kontekst/decyzję/skutki, linię statusu
    i wiersz w indeksie, numeracja nie ma dziur, a źródła wskazują istniejące pliki —
    inaczej rejestr gnije jak rozproszone notatki, które zastąpił."""

    ADR_DIR = ROOT / "docs" / "adr"
    SECTIONS = ("Kontekst", "Decyzja", "Skutki", "Źródła")
    STATUS_RE = re.compile(r"^- \*\*Status:\*\* (Proponowana|Przyjęta|Odrzucona|Wycofana"
                           r"|Zastąpiona przez ADR-(\d{4}))$", re.M)
    # Ścieżka w backtickach względem korzenia repo, opcjonalnie z numerem linii (`a/b.md:12`).
    PATH_RE = re.compile(r"`([\w./-]+\.(?:md|py|js|json|ya?ml|sh|toml|txt|csv|html|bat))(?::\d+)?`")
    LINK_RE = re.compile(r"\]\(([^)\s]+)\)")

    def _adrs(self):
        return sorted(self.ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))

    def _missing_links(self, path, text):
        return [f"{path.name} → {t}" for t in self.LINK_RE.findall(text)
                if "://" not in t and not t.startswith("#")
                and not (path.parent / t.split("#")[0]).exists()]

    def test_register_exists_and_is_linked(self):
        self.assertTrue(self._adrs())
        self.assertIn("adr/README.md", (ROOT / "docs" / "README.md").read_text(encoding="utf-8"))
        self.assertIn("docs/adr/README.md", (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8"))

    def test_each_adr_has_title_status_date_and_sections(self):
        for path in self._adrs():
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith(f"# ADR-{path.name[:4]}: "), path.name)
            self.assertRegex(text, self.STATUS_RE, path.name)
            self.assertRegex(text, r"(?m)^- \*\*Data:\*\* \d{4}-\d{2}-\d{2}$", path.name)
            for section in self.SECTIONS:
                self.assertRegex(text, rf"(?m)^## {section}\s*$", f"{path.name}: brak ## {section}")

    def test_numbers_unique_and_consecutive(self):
        numbers = [int(p.name[:4]) for p in self._adrs()]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    def test_every_adr_indexed_with_its_status(self):
        index = (self.ADR_DIR / "README.md").read_text(encoding="utf-8")
        rows = [line for line in index.splitlines() if line.startswith("|")]
        for path in self._adrs():
            row = next((r for r in rows if f"]({path.name})" in r), None)
            self.assertIsNotNone(row, f"{path.name}: brak wiersza w indeksie docs/adr/README.md")
            status = self.STATUS_RE.search(path.read_text(encoding="utf-8"))
            self.assertIn(f"| {status.group(1)} |", row, path.name)
            if status.group(2):     # „Zastąpiona przez ADR-NNNN” → NNNN musi istnieć
                self.assertTrue(list(self.ADR_DIR.glob(f"{status.group(2)}-*.md")), path.name)
        self.assertEqual(self._missing_links(self.ADR_DIR / "README.md", index), [])

    def test_sources_and_links_exist(self):
        missing = []
        for path in self._adrs():
            text = path.read_text(encoding="utf-8")
            sources = text.split("\n## Źródła", 1)[1].split("\n## ", 1)[0]
            missing += [f"{path.name} → {ref}" for ref in self.PATH_RE.findall(sources)
                        if not (ROOT / ref).is_file()]
            missing += self._missing_links(path, text)
        self.assertEqual(missing, [])
