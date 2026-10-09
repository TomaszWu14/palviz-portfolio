"""SEC-001: nagłówek CSP wskazuje report-uri, a /csp-report/ przyjmuje raporty."""
import json

from django.core.cache import cache
from django.test import TestCase

URL = "/csp-report/"
REPORT = {"csp-report": {"document-uri": "https://x/login/", "violated-directive": "script-src",
                         "blocked-uri": "https://evil.example/a.js"}}


class CspReportTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_header_has_report_uri(self):
        r = self.client.get("/login/")
        csp = r.headers.get("Content-Security-Policy-Report-Only") or r.headers.get("Content-Security-Policy", "")
        self.assertIn("report-uri /csp-report/", csp)

    def test_post_report_logs_and_returns_204(self):
        with self.assertLogs("core.csp_report", "WARNING") as cm:
            r = self.client.post(URL, json.dumps(REPORT), content_type="application/csp-report")
        self.assertEqual(r.status_code, 204)
        rec = cm.records[0]
        self.assertEqual(rec.getMessage(), "CSP violation")
        self.assertEqual(rec.blocked_uri, "https://evil.example/a.js")
        self.assertEqual(rec.violated_directive, "script-src")

    def test_reporting_api_format(self):
        body = [{"type": "csp-violation", "body": {"blockedURL": "inline", "effectiveDirective": "script-src-elem",
                                                   "documentURL": "https://x/"}}]
        with self.assertLogs("core.csp_report", "WARNING") as cm:
            r = self.client.post(URL, json.dumps(body), content_type="application/reports+json")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(cm.records[0].violated_directive, "script-src-elem")

    def test_too_large_body_413(self):
        r = self.client.post(URL, "x" * (17 * 1024), content_type="application/csp-report")
        self.assertEqual(r.status_code, 413)

    def test_get_405(self):
        self.assertEqual(self.client.get(URL).status_code, 405)

    def test_rate_limit_per_ip(self):
        with self.assertLogs("core.csp_report", "WARNING"):
            codes = [self.client.post(URL, json.dumps(REPORT), content_type="application/csp-report").status_code
                     for _ in range(61)]
        self.assertEqual(codes[-1], 429)
        self.assertEqual(codes[0], 204)
