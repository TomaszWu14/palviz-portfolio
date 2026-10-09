"""STRUCT-04 + STRUCT-02: strażnik limitu 500 linii na plik.

Dowodzi, że `file_size_check.find_oversized` łapie plik >500 linii, honoruje
wyjątki (migrations/, vendor/, settings.py) i zgłasza czyste drzewo na
prawdziwym repo (STRUCT-02 — brama zgodności)."""
import importlib.util
import pathlib
import tempfile

from django.test import SimpleTestCase

_ROOT = pathlib.Path(__file__).resolve().parents[3]  # repo root
_SCRIPT = _ROOT / "web" / "scripts" / "file_size_check.py"

_spec = importlib.util.spec_from_file_location("file_size_check", _SCRIPT)
file_size_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(file_size_check)

find_oversized = file_size_check.find_oversized


def _write_lines(path: pathlib.Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"# line {i}" for i in range(count)) + "\n", encoding="utf-8")


class FileSizeGateTests(SimpleTestCase):
    def test_flags_oversized_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write_lines(root / "big.py", 600)
            offenders = find_oversized([root])
            self.assertEqual(len(offenders), 1)
            self.assertEqual(offenders[0][1], 600)

    def test_does_not_flag_small_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write_lines(root / "small.py", 100)
            self.assertEqual(find_oversized([root]), [])

    def test_exempts_migrations_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write_lines(root / "app" / "migrations" / "0001_big.py", 600)
            self.assertEqual(find_oversized([root]), [])

    def test_exempts_vendor_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write_lines(root / "static" / "vendor" / "big.py", 600)
            self.assertEqual(find_oversized([root]), [])

    def test_exempts_settings_py(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write_lines(root / "palletweb" / "settings.py", 600)
            self.assertEqual(find_oversized([root]), [])

    def test_real_tree_is_clean(self):
        # STRUCT-02: brama zgłasza czyste drzewo na prawdziwym repo.
        offenders = find_oversized([_ROOT / "web", _ROOT / "palletizer"])
        self.assertEqual(offenders, [], f"Znaleziono zbyt duże pliki: {offenders}")
