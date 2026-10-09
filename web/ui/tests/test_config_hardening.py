"""Utwardzenie konfiguracji z audytu: INT-001, GDPR-003, SEC-002, OBS-002, TEST-001."""
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from axes.models import AccessAttempt
from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from core.middleware import client_ip

REDIS = {"default": {"BACKEND": "django_redis.cache.RedisCache", "LOCATION": "redis://x"}}


class SettingsHardeningTests(SimpleTestCase):
    def test_email_timeout_set(self):  # INT-001
        self.assertEqual(settings.EMAIL_TIMEOUT, 20)

    def test_sentry_drops_local_variables_and_pii(self):  # GDPR-003
        self.assertIs(settings.SENTRY_INIT_OPTS["include_local_variables"], False)
        self.assertIs(settings.SENTRY_INIT_OPTS["send_default_pii"], False)


class ClientIpTests(SimpleTestCase):  # SEC-002
    rf = RequestFactory()

    def test_last_forwarded_entry_wins(self):
        req = self.rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 1.2.3.4", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(client_ip(req), "1.2.3.4")  # pierwszy wpis mógł podrobić klient

    def test_no_header_falls_back_to_remote_addr(self):
        self.assertEqual(client_ip(self.rf.get("/", REMOTE_ADDR="10.0.0.1")), "10.0.0.1")


@override_settings(AXES_CLIENT_IP_CALLABLE="core.middleware.client_ip")
class AxesBehindProxyTests(TestCase):  # SEC-002 end-to-end
    def test_attempts_counted_per_forwarded_ip(self):
        for ip in ("1.1.1.1", "2.2.2.2"):
            self.client.post("/login/", {"username": "joe", "password": "zle"},
                             HTTP_X_FORWARDED_FOR=ip, REMOTE_ADDR="10.0.0.1")
        self.assertEqual(
            sorted(AccessAttempt.objects.values_list("ip_address", flat=True)),
            ["1.1.1.1", "2.2.2.2"])


class HealthCacheTests(TestCase):  # OBS-002
    def test_locmem_cache_not_reported(self):
        r = self.client.get("/health/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("cache", r.json())
        self.assertIn("version", r.json())  # czyta smoke w deploy.yml

    @override_settings(CACHES=REDIS)
    def test_redis_error_is_degraded_but_200(self):
        # 200, żeby Docker HEALTHCHECK nie restartował działającej apki; monitor łapie "degraded"
        with mock.patch("core.health_urls.cache") as c:
            c.set.side_effect = ConnectionError("redis down")
            r = self.client.get("/health/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "degraded")
        self.assertEqual(r.json()["cache"], "error")
        self.assertEqual(r.json()["db"], "ok")

    @override_settings(CACHES=REDIS)
    def test_redis_ok(self):
        store = {}
        with mock.patch("core.health_urls.cache") as c:
            c.set.side_effect = lambda k, v, t: store.__setitem__(k, v)
            c.get.side_effect = store.get
            r = self.client.get("/health/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        self.assertEqual(r.json()["cache"], "ok")

    def test_db_down_still_503(self):
        with mock.patch("core.health_urls.connection.ensure_connection", side_effect=Exception("db")):
            r = self.client.get("/health/")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["db"], "error")


class TestCountGuardTests(SimpleTestCase):  # TEST-001
    script = Path(settings.BASE_DIR) / "scripts" / "test_count_check.py"

    def _run(self, ran):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "log.txt"
            log.write_text(f"....\nRan {ran} tests in 60.0s\n\nOK\n", encoding="utf-8")
            return subprocess.run([sys.executable, str(self.script), str(log)],
                                  capture_output=True).returncode

    def test_floor_raised(self):
        baseline = (self.script.parent / "test_count_baseline.txt").read_text()
        self.assertGreaterEqual(int(baseline), 1850)

    def test_guard(self):
        self.assertEqual(self._run(1884), 0)
        self.assertEqual(self._run(1500), 1)
