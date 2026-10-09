"""Szew OUT do n8n: emit_event best-effort (no-op bez URL, nie wywala zapisu domenowego)."""
import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from ui.models import Task
from ui.notifications import emit_event, _raise_task


class EmitEventTest(TestCase):
    @override_settings(N8N_EVENT_URL="")
    def test_noop_when_url_empty(self):
        with patch("urllib.request.urlopen") as m:
            self.assertFalse(emit_event("x", {"a": 1}))
            m.assert_not_called()

    @override_settings(N8N_EVENT_URL="http://n8n.local/webhook", N8N_EVENT_SECRET="s3cret")
    def test_posts_payload_and_secret_when_configured(self):
        with patch("urllib.request.urlopen", return_value=None) as m:
            self.assertTrue(emit_event("stock_task", {"dedup_key": "k1"}))
            req = m.call_args.args[0]
            self.assertEqual(json.loads(req.data)["kind"], "stock_task")
            # urllib kapitalizuje nazwy nagłówków → "X-n8n-secret"
            self.assertEqual(req.headers.get("X-n8n-secret"), "s3cret")

    @override_settings(N8N_EVENT_URL="http://n8n.local/webhook")
    def test_transport_failure_does_not_break_caller(self):
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            # zadanie MUSI powstać mimo padniętego webhooka
            self.assertTrue(_raise_task("Brak danych", "opis", "src", "stock:nodata:1",
                                        "/tasks/", recipients=[]))
        self.assertEqual(Task.objects.filter(dedup_key="stock:nodata:1").count(), 1)


class RaiseTaskEmitPayloadTest(TestCase):
    @override_settings(N8N_EVENT_URL="http://n8n.local/webhook")
    def test_payload_is_minimal_and_non_sensitive(self):
        with patch("ui.notifications.emit_event") as mock_emit:
            _raise_task("Tytuł", "opis wrażliwy klienta", "src", "stock:nodata:9", "/u",
                        recipients=[])
        mock_emit.assert_called_once()
        kind, payload = mock_emit.call_args.args
        self.assertEqual(kind, "stock_task")
        # tylko whitelist pól — bez opisu/klienta
        self.assertEqual(set(payload), {"dedup_key", "source_ref", "title", "ref_code", "location"})
        self.assertNotIn("opis wrażliwy", json.dumps(payload, ensure_ascii=False))
