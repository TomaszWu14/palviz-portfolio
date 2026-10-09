"""Etap 1 — strażnicy infrastruktury testów: blokada sieci, zegar Europe/Warsaw, FakeHTTP.

Działa w ``manage.py test`` i w pytest (``pytest web/ui/tests/test_testkit_infra.py``).
"""
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone as dt_timezone

from django.test import SimpleTestCase
from django.utils import timezone

from testkit import net
from testkit.clock import KEY_DATES, WARSAW, frozen, key_dates, last_sunday
from testkit.fake_http import FakeHTTP, Reply


class NetworkGuardTests(SimpleTestCase):
    def test_guard_installed_for_the_whole_run(self):
        self.assertTrue(net.is_installed(), "TEST_RUNNER/conftest nie zainstalował blokady sieci")

    def test_external_connection_blocked_before_any_packet(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s.close)
        with self.assertRaises(net.NetworkBlocked):
            s.connect(("192.0.2.10", 443))         # TEST-NET-1: nigdy nie routowany
        self.assertIn(("192.0.2.10", 443), net.blocked)

    def test_connect_ex_and_hostnames_also_blocked(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s.close)
        with self.assertRaises(net.NetworkBlocked):
            s.connect_ex(("example.com", 80))

    def test_real_http_libraries_hit_the_guard(self):
        import requests
        # NetworkBlocked to RuntimeError, nie OSError — biblioteki go nie przepakowują w „brak sieci”
        with self.assertRaises(net.NetworkBlocked):
            requests.get("http://192.0.2.10/", timeout=1)
        with self.assertRaises(net.NetworkBlocked):
            urllib.request.urlopen("http://192.0.2.11/", timeout=1)

    def test_fast_password_hasher_active(self):
        from django.conf import settings
        from django.contrib.auth.hashers import make_password
        from testkit.runner import FAST_HASHER
        self.assertEqual(settings.PASSWORD_HASHERS[0], FAST_HASHER)
        self.assertTrue(make_password("x").startswith("md5$"))

    def test_loopback_allowed(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(srv.close)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        cli = socket.create_connection(srv.getsockname(), timeout=2)
        cli.close()


class ClockTests(SimpleTestCase):
    def test_last_sundays_2026(self):
        self.assertEqual(str(last_sunday(2026, 3)), "2026-03-29")
        self.assertEqual(str(last_sunday(2026, 10)), "2026-10-25")
        self.assertEqual(str(last_sunday(2027, 3)), "2027-03-28")

    def test_dst_spring_gap_is_one_real_minute(self):
        d = KEY_DATES
        utc = [d[k].astimezone(dt_timezone.utc) for k in ("dst_wiosna_przed", "dst_wiosna_po")]
        self.assertEqual((utc[1] - utc[0]).total_seconds(), 60)   # zegar ścienny: 1 h 1 min
        self.assertEqual(d["dst_wiosna_przed"].utcoffset().total_seconds(), 3600)
        self.assertEqual(d["dst_wiosna_po"].utcoffset().total_seconds(), 7200)

    def test_dst_autumn_repeated_hour(self):
        first, second = KEY_DATES["dst_jesien"], KEY_DATES["dst_jesien_drugi_raz"]
        self.assertEqual(first.replace(tzinfo=None), second.replace(tzinfo=None))
        self.assertEqual((second.astimezone(dt_timezone.utc) - first.astimezone(dt_timezone.utc))
                         .total_seconds(), 3600)

    def test_frozen_now_and_localdate_follow_warsaw(self):
        with frozen("koniec_roku"):
            self.assertEqual(timezone.localtime().replace(tzinfo=None),
                             datetime(2026, 12, 31, 23, 59, 59))
            self.assertEqual(str(timezone.localdate()), "2026-12-31")
            # w UTC to jeszcze 22:59 — lokalna data musi pochodzić z Europe/Warsaw
            self.assertEqual(timezone.now().astimezone(dt_timezone.utc).hour, 22)
        with frozen("nowy_rok"):
            self.assertEqual(str(timezone.localdate()), "2027-01-01")

    def test_frozen_accepts_naive_iso_as_warsaw_and_stops_clock(self):
        with frozen("2026-10-25T02:30:00"):
            a = timezone.now()
            b = timezone.now()
            self.assertEqual(a, b)
            self.assertEqual(timezone.localtime(a).tzinfo.key, WARSAW.key)

    def test_key_dates_for_any_year(self):
        self.assertEqual(key_dates(2030)["dst_jesien"].day, last_sunday(2030, 10).day)


class FakeHTTPTests(SimpleTestCase):
    def test_same_route_for_requests_urllib_and_httpx(self):
        import httpx
        import requests
        with FakeHTTP() as http:
            http.add(r"svc\.example\.test", Reply(json={"ok": 1}))
            self.assertEqual(requests.get("https://svc.example.test/a").json(), {"ok": 1})
            with urllib.request.urlopen("https://svc.example.test/b") as r:
                self.assertEqual(r.status, 200)
                self.assertEqual(r.read(), b'{"ok": 1}')
            self.assertEqual(httpx.Client().post("https://svc.example.test/c", json={}).json(), {"ok": 1})
        self.assertEqual([c.method for c in http.calls], ["GET", "GET", "POST"])

    def test_errors_map_to_each_library_native_exception(self):
        import httpx
        import requests
        with FakeHTTP() as http:
            http.add(r"//t\.", Reply(raises="timeout")).add(r"//e\.", Reply(status=503))
            with self.assertRaises(requests.exceptions.Timeout):
                requests.get("https://t.example.test")
            with self.assertRaises(TimeoutError):
                urllib.request.urlopen("https://t.example.test")
            with self.assertRaises(httpx.TimeoutException):
                httpx.get("https://t.example.test")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen("https://e.example.test")
            self.assertEqual(ctx.exception.code, 503)
            self.assertEqual(requests.get("https://e.example.test").status_code, 503)

    def test_unmatched_request_fails_loudly(self):
        import requests
        with FakeHTTP(), self.assertRaises(AssertionError):
            requests.get("https://nieznany.example.test")

    def test_patches_removed_after_block(self):
        import requests.adapters
        before = requests.adapters.HTTPAdapter.send
        with FakeHTTP():
            self.assertIsNot(requests.adapters.HTTPAdapter.send, before)
        self.assertIs(requests.adapters.HTTPAdapter.send, before)
