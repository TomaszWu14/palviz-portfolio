"""Media-serving + upload hardening: nosniff/download disposition, login gate for
internal photos, and the HU-photo validation helper."""
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

_TMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=_TMP)
class MediaServeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import os
        for rel in ("quotes/2026/07", "quality/2026/07"):
            os.makedirs(os.path.join(_TMP, rel), exist_ok=True)
        # An "active content" upload and a raster image.
        open(os.path.join(_TMP, "quotes/2026/07/evil.html"), "w").write("<script>alert(1)</script>")
        open(os.path.join(_TMP, "quotes/2026/07/ok.png"), "wb").write(b"\x89PNG\r\n\x1a\n")
        open(os.path.join(_TMP, "quality/2026/07/photo.jpg"), "wb").write(b"\xff\xd8\xff")

    def test_html_upload_forced_to_download_with_nosniff(self):
        r = self.client.get("/media/quotes/2026/07/evil.html")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])      # cannot render as HTML
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")

    def test_image_stays_inline(self):
        r = self.client.get("/media/quotes/2026/07/ok.png")
        self.assertEqual(r.status_code, 200)
        # Django's FileResponse marks images "inline" — we must NOT force a download.
        self.assertNotIn("attachment", r.get("Content-Disposition", ""))
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")

    def test_internal_photo_requires_login(self):
        r = self.client.get("/media/quality/2026/07/photo.jpg")
        self.assertEqual(r.status_code, 302)                       # → login
        self.assertIn("/login", r["Location"])

    def test_internal_photo_served_when_logged_in(self):
        u = get_user_model().objects.create_user("op", password="x")
        self.client.force_login(u)
        r = self.client.get("/media/quality/2026/07/photo.jpg")
        self.assertEqual(r.status_code, 200)


class PhotoValidationTests(TestCase):
    def _req(self, upload):
        rf = RequestFactory()
        req = rf.post("/", {"photo": upload} if upload else {})
        req.session = {}
        req._messages = FallbackStorage(req)
        return req

    def test_valid_image_accepted(self):
        from huctl.views.hu_control import _valid_photo
        f = SimpleUploadedFile("p.jpg", b"\xff\xd8\xff", content_type="image/jpeg")
        self.assertIsNotNone(_valid_photo(self._req(f)))

    def test_non_image_rejected(self):
        from huctl.views.hu_control import _valid_photo
        f = SimpleUploadedFile("p.html", b"<script>", content_type="text/html")
        self.assertIsNone(_valid_photo(self._req(f)))

    def test_oversized_rejected(self):
        from huctl.views.hu_control import _valid_photo, _PHOTO_MAX_BYTES
        big = SimpleUploadedFile("p.jpg", b"x" * (_PHOTO_MAX_BYTES + 1), content_type="image/jpeg")
        self.assertIsNone(_valid_photo(self._req(big)))
