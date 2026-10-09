"""Strażnik szybkiego deployu (audyt Docker/Coolify, PR #699).

Coolify podaje nowy ``SOURCE_COMMIT`` przy każdym deployu. ``ARG``/``ENV`` z tą wartością
unieważnia cache WSZYSTKICH instrukcji po nim — stojąc przed ``apt-get`` sprawiał, że każdy
deploy przebudowywał pakiety systemowe i kopiował venv 916 MB (145 s zamiast ~20 s).
"""
import os
import re

from django.test import SimpleTestCase

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BUILD_STEPS = ("RUN", "COPY", "ADD")


def _instructions(path):
    """[(INSTRUKCJA, reszta)] z pominięciem komentarzy; linie kontynuowane ``\\`` sklejone."""
    text = open(path, encoding="utf-8").read().replace("\\\n", " ")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            word, _, rest = line.partition(" ")
            out.append((word.upper(), rest))
    return out


class DockerfileCacheGuardTests(SimpleTestCase):
    def setUp(self):
        self.steps = _instructions(os.path.join(REPO, "Dockerfile"))
        last_from = max(i for i, (w, _) in enumerate(self.steps) if w == "FROM")
        self.runtime = self.steps[last_from:]

    def test_commit_sha_comes_after_every_build_step(self):
        sha = [i for i, (w, rest) in enumerate(self.runtime)
               if w == "ARG" and rest.startswith("SOURCE_COMMIT")]
        self.assertEqual(len(sha), 1, "ARG SOURCE_COMMIT musi być w etapie runtime dokładnie raz")
        later = [w for w, _ in self.runtime[sha[0]:] if w in BUILD_STEPS]
        self.assertEqual(later, [], "po ARG SOURCE_COMMIT nie może być RUN/COPY/ADD — zabija cache deployu")

    def test_sha_not_declared_in_earlier_stages(self):
        before_runtime = self.steps[:len(self.steps) - len(self.runtime)]
        self.assertFalse(any(w in ("ARG", "ENV") and "SOURCE_COMMIT" in rest for w, rest in before_runtime))

    def test_dependencies_installed_before_code_is_copied(self):
        order = [(w, rest) for w, rest in self.runtime if w in BUILD_STEPS]
        venv = next(i for i, (w, rest) in enumerate(order) if "/opt/venv" in rest)
        code = next(i for i, (w, rest) in enumerate(order) if re.match(r"(--\S+\s+)*web/", rest))
        self.assertLess(venv, code)


class DockerignoreGuardTests(SimpleTestCase):
    def test_heavy_and_test_paths_excluded_from_context(self):
        rules = {line.strip() for line in open(os.path.join(REPO, ".dockerignore"), encoding="utf-8")}
        for required in (".git", "graphify-out", "web/*/tests", "web/testkit", ".venv"):
            with self.subTest(rule=required):
                self.assertIn(required, rules)
