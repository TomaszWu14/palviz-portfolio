"""Załącznik pominięty (obraz na modelu bez vision) daje widoczny warning w strumieniu SSE
— nie znika po cichu."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import ZariaConversation, ZariaModel


class ZariaAttachWarningTest(TestCase):
    def setUp(self):
        cache.clear()
        self.u = User.objects.create_superuser("attw", "a@a.pl", "x")   # przechodzi bramkę modułu i dostęp do modelu
        self.model = ZariaModel.objects.create(
            key="local-llm", display_name="Lokalny", provider="ollama",
            price_input_per_1k="0", price_output_per_1k="0")
        self.conv = ZariaConversation.objects.create(user=self.u, model=self.model)
        self.client.force_login(self.u)

    @patch("ui.zaria_llm.stream_complete")
    def test_skipped_image_yields_warning(self, mock_stream):
        mock_stream.return_value = iter([("done", 1, 1)])
        img = SimpleUploadedFile("foto.png", b"\x89PNGfake", content_type="image/png")
        resp = self.client.post(reverse("ui:zaria_send_stream", args=[self.conv.pk]),
                                {"content": "opisz obraz", "files": img})
        body = b"".join(resp.streaming_content).decode()
        self.assertIn("warning", body)      # emitowany event SSE
        self.assertIn("obraz", body)        # treść: „obraz pominięty…"
