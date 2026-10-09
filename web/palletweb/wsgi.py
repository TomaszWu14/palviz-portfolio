import os
import sys
from pathlib import Path
from django.core.wsgi import get_wsgi_application

ROOT_DIR = Path(__file__).resolve().parents[2]  # ../../ (root projektu)
sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "palletweb.settings")
application = get_wsgi_application()
