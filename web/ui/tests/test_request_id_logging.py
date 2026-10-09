"""OBS-003: request-id z X-Request-ID trafia do logów stdlib (logging.getLogger)."""
import logging
from io import StringIO

from django.conf import settings
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import path

from core.middleware import RequestIDLogFilter, request_id_var

log = logging.getLogger("ui.tests.request_id")


def _logging_view(request):
    log.warning("widok testowy")
    return HttpResponse("ok")


urlpatterns = [path("rid-log/", _logging_view)]


@override_settings(ROOT_URLCONF=__name__)
class RequestIdLoggingTests(TestCase):
    def setUp(self):
        self.buf = StringIO()
        fmt = settings.LOGGING["formatters"]["verbose"]
        handler = logging.StreamHandler(self.buf)
        handler.setFormatter(logging.Formatter(fmt["format"], style=fmt["style"]))
        handler.addFilter(RequestIDLogFilter())
        log.addHandler(handler)
        self.addCleanup(log.removeHandler, handler)

    def test_log_line_contains_request_id_from_header(self):
        r = self.client.get("/rid-log/", HTTP_X_REQUEST_ID="abc123")
        self.assertEqual(r["X-Request-ID"], "abc123")
        self.assertIn("[abc123] ui.tests.request_id: widok testowy", self.buf.getvalue())

    def test_request_id_cleared_after_response(self):
        self.client.get("/rid-log/", HTTP_X_REQUEST_ID="xyz")
        self.assertEqual(request_id_var.get(), "-")
        log.warning("poza żądaniem")
        self.assertIn("[-] ui.tests.request_id: poza żądaniem", self.buf.getvalue())
