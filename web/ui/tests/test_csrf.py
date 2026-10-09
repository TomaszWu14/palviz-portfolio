"""A stale CSRF token shows a friendly 'session expired' page (re-login), not the bare 403."""
from django.test import TestCase, Client


class CsrfFailureTests(TestCase):
    def test_friendly_csrf_failure_page(self):
        c = Client(enforce_csrf_checks=True)
        resp = c.post("/login/", {"username": "x", "password": "y"})
        self.assertEqual(resp.status_code, 403)
        body = resp.content.decode()
        self.assertIn("Sesja wygasła", body)
        self.assertIn("/login/", body)
