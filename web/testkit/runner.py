"""TEST_RUNNER dla ``manage.py test``: DiscoverRunner + blokada sieci + szybki hasher haseł
+ informacja o bazie.

Hasher: produkcyjny PBKDF2 (1 mln iteracji) to ~1,5 s na każde ``create_user``/``set_password`` —
w testach MD5 (hasła testowe nic nie chronią). Produkcja bez zmian.

Blokada musi działać także w procesach roboczych ``--parallel``: przy ``fork`` (Linux/CI)
dziedziczą ją z procesu głównego, przy ``spawn`` (Windows/macOS) instaluje ją
``process_setup`` każdego procesu.
"""
import sys

from django.conf import settings
from django.db import connection
from django.test.runner import DiscoverRunner, ParallelTestSuite

from . import net


FAST_HASHER = "django.contrib.auth.hashers.MD5PasswordHasher"
PLAIN_STATIC = "django.contrib.staticfiles.storage.StaticFilesStorage"


def prepare():
    """Wspólne dla manage.py test (proces główny i robocze) i pytest (conftest.py)."""
    net.install()
    if settings.PASSWORD_HASHERS[0] != FAST_HASHER:
        settings.PASSWORD_HASHERS = [FAST_HASHER, *settings.PASSWORD_HASHERS]
    # Prod: statyki z manifestem (hash w nazwie) — wymaga collectstatic, którego testy nie robią.
    # Istnienie plików z {% static %} pilnuje ui.tests.test_static_manifest.
    if settings.STORAGES["staticfiles"]["BACKEND"] != PLAIN_STATIC:
        settings.STORAGES = {**settings.STORAGES, "staticfiles": {"BACKEND": PLAIN_STATIC}}


def _worker_setup(*_args):
    prepare()


class _ParallelSuite(ParallelTestSuite):
    process_setup = _worker_setup


class GrooveTestRunner(DiscoverRunner):
    parallel_test_suite = _ParallelSuite

    def setup_test_environment(self, **kwargs):
        prepare()
        super().setup_test_environment(**kwargs)

    def setup_databases(self, **kwargs):
        config = super().setup_databases(**kwargs)
        if self.verbosity >= 1:
            ver = ".".join(str(v) for v in connection.get_database_version())
            sys.stderr.write(f"[testkit] baza testowa: {connection.vendor} {ver}\n")
        return config
