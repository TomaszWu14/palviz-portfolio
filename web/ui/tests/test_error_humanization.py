"""UX #7: surowe błędy techniczne / ścieżki adminowe nie wyciekają do UI."""
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class AxesLockoutTemplateTests(SimpleTestCase):
    """Strona blokady po nieudanych logowaniach jest widoczna dla dowolnego
    (niezalogowanego) usera — nie może pokazywać ścieżek panelu Django Admin."""

    def test_no_admin_path_leaked(self):
        html = render_to_string("ui/axes_lockout.html", {"app_name": "GROOVE"})
        self.assertNotIn("/admin/axes/accessattempt/", html)
        self.assertNotIn("/admin/login/", html)
        self.assertIn("skontaktuj się z administratorem", html)
