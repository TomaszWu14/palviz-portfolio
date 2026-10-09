import os
import sys
from pathlib import Path

from celery import Celery

# Jak w wsgi.py/manage.py: root repo na sys.path, bo aplikacja importuje pakiet `palletizer`
# (CLI `celery -A palletweb` startuje z web/ i bez tego worker pada na ModuleNotFoundError).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "palletweb.settings")

app = Celery("palletweb")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
