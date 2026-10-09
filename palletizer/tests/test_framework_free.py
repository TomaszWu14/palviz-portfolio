"""Pakiet `palletizer` bez zależności od Django (ADR-0002).

CLI (`main.py`) i ten zestaw testów działają bez Django — import `django` w pakiecie
zepsułby oba po cichu dopiero przy instalacji bez aplikacji webowej."""
import ast
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]


def _django_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        found += [f"{path.relative_to(PKG.parent)}:{node.lineno} {n}"
                  for n in names if n == "django" or n.startswith("django.")]
    return found


class FrameworkFreeTests(unittest.TestCase):
    def test_no_django_imports(self):
        offenders = [hit for f in sorted(PKG.rglob("*.py")) for hit in _django_imports(f)]
        self.assertEqual(offenders, [], "palletizer/ musi zostać wolny od Django (ADR-0002)")

    def test_guard_detects_django_import(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=PKG) as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text("def f():\n    from django.conf import settings\n", encoding="utf-8")
            self.assertEqual(len(_django_imports(probe)), 1)


if __name__ == "__main__":
    unittest.main()
