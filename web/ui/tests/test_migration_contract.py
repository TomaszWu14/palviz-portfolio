"""BUILD-001: migracje expand → contract.

Entrypoint migruje bazę, gdy stara wersja jeszcze działa — operacja niszcząca (usunięcie /
zmiana nazwy kolumny lub modelu) w tym samym deployu co zmiana kodu psuje stary kontener.
Nowa migracja z taką operacją musi mieć komentarz `# contract:` (np. `# contract: użycia
usunięte w PR #123`) — świadome potwierdzenie, że kod już jej nie potrzebuje.
Migracje do BASELINE włącznie są historią i nie są sprawdzane."""
import pathlib
import re

from django.test import SimpleTestCase

WEB = pathlib.Path(__file__).resolve().parents[2]
BASELINE = {"ui": 192, "wh3d": 4, "huctl": 4, "transport": 2}
DESTRUCTIVE = re.compile(r"migrations\.(RemoveField|DeleteModel|RenameField|RenameModel)\(")


def offenders(web=WEB, baseline=BASELINE):
    bad = []
    for app, last in baseline.items():
        for p in sorted((web / app / "migrations").glob("[0-9][0-9][0-9][0-9]_*.py")):
            if int(p.name[:4]) <= last:
                continue
            text = p.read_text(encoding="utf-8")
            if DESTRUCTIVE.search(text) and "# contract:" not in text:
                bad.append(f"{app}/{p.name}")
    return bad


class MigrationContractTests(SimpleTestCase):
    def test_destructive_migrations_are_marked(self):
        self.assertEqual(offenders(), [], "Operacja contract bez komentarza `# contract:` — "
                                         "patrz ARCHITECTURE.md §Migracje expand → contract")

    def test_guard_detects_unmarked_removal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            mig = pathlib.Path(d) / "ui" / "migrations"
            mig.mkdir(parents=True)
            (mig / "0001_old.py").write_text("migrations.RemoveField(\n", encoding="utf-8")
            (mig / "0002_new.py").write_text("migrations.RemoveField(\n", encoding="utf-8")
            (mig / "0003_ok.py").write_text("# contract: PR #1\nmigrations.DeleteModel(\n",
                                            encoding="utf-8")
            self.assertEqual(offenders(pathlib.Path(d), {"ui": 1}), ["ui/0002_new.py"])
