#!/usr/bin/env python
"""Pre-commit: zablokuj commit, gdy zmiana modeli nie ma migracji.

Uruchamia `manage.py makemigrations --check --dry-run` z env testowym CI.
"""
import os
import subprocess
import sys

WEB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(WEB)
PY = sys.executable
for cand in (
    os.path.join(ROOT, ".venv", "Scripts", "python.exe"),
    os.path.join(ROOT, ".venv", "bin", "python"),
):
    if os.access(cand, os.X_OK):
        PY = cand
        break
env = dict(
    os.environ,
    DJANGO_SECRET_KEY="ci-test-secret",
    DJANGO_DEBUG="true",
    DJANGO_ALLOWED_HOSTS="*",
    PYTHONPATH=ROOT,
)
sys.exit(
    subprocess.call(
        [PY, "manage.py", "makemigrations", "--check", "--dry-run"],
        cwd=WEB,
        env=env,
    )
)
