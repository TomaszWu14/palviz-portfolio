import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]  # ../ (root projektu)
WEB_DIR = Path(__file__).resolve().parent       # ./web

sys.path.insert(0, str(ROOT_DIR))

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "palletweb.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
