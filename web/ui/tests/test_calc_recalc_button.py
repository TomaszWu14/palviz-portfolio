"""Regresja: przycisk „Przelicz” w kalkulatorze paletyzacji (/planner/calc/).

Diagnoza: formularz #manual-form miał ``hx-trigger="change …, input …"`` — jawny
``hx-trigger`` ZASTĘPUJE domyślny trigger formularza (``submit``), więc htmx nie
przechwytywał kliknięcia „Przelicz”. Przeglądarka robiła natywny submit formularza
bez ``method``/``action`` → GET na /planner/calc/ z ``csrfmiddlewaretoken`` w query
stringu (wyciek tokenu do logów/historii/Referer) i zwykłe przeładowanie bez wyniku.
"""
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase


class _FormCollector(HTMLParser):
    """Zbiera atrybuty każdego <form> i nazwy pól w jego wnętrzu."""

    def __init__(self):
        super().__init__()
        self.forms = []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            form = {"attrs": attrs, "fields": [], "buttons": []}
            self.forms.append(form)
            self._stack.append(form)
        elif self._stack and tag in ("input", "select", "textarea"):
            self._stack[-1]["fields"].append(attrs.get("name"))
        elif self._stack and tag == "button":
            self._stack[-1]["buttons"].append(attrs)

    def handle_endtag(self, tag):
        if tag == "form" and self._stack:
            self._stack.pop()


def _forms(html):
    p = _FormCollector()
    p.feed(html)
    return p.forms


CALC_DATA = dict(pallet="EU", max_height_total="215", max_weight="1000", sku="S", variant="STD",
                 carton_l="40", carton_w="30", carton_h="25", unit_weight="0.45",
                 pcs_per_carton="24", demand_pcs="100", carton_tare="0.2", render_layers="3")


class CalcRecalcButtonTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser(username="calc", password="x")
        self.client.force_login(user)

    def _manual_form(self):
        r = self.client.get("/planner/calc/")
        self.assertEqual(r.status_code, 200)
        forms = [f for f in _forms(r.content.decode()) if f["attrs"].get("id") == "manual-form"]
        self.assertEqual(len(forms), 1)
        return forms[0]

    def test_csrf_token_never_in_get_form(self):
        """Żaden formularz GET (jawny lub domyślny) nie niesie csrfmiddlewaretoken."""
        r = self.client.get("/planner/calc/")
        for form in _forms(r.content.decode()):
            method = (form["attrs"].get("method") or "get").lower()
            if method != "post":
                self.assertNotIn("csrfmiddlewaretoken", form["fields"],
                                 f"Formularz GET z tokenem CSRF: {form['attrs']}")

    def test_manual_form_posts_to_calculate(self):
        """Pełny submit (bez JS / bez htmx) idzie POST-em do endpointu przeliczenia."""
        attrs = self._manual_form()["attrs"]
        self.assertEqual((attrs.get("method") or "").lower(), "post")
        self.assertEqual(attrs.get("action"), "/planner/calc/run/")

    def test_przelicz_triggers_htmx_submit(self):
        """„Przelicz” to submit, a hx-trigger formularza obejmuje zdarzenie submit."""
        form = self._manual_form()
        attrs = form["attrs"]
        self.assertEqual(attrs.get("hx-post"), "/planner/calc/run/")
        self.assertEqual(attrs.get("hx-target"), "#result-area")
        triggers = [t.strip().split(" ")[0] for t in attrs.get("hx-trigger", "").split(",")]
        self.assertIn("submit", triggers)
        self.assertIn("change", triggers)          # auto-przeliczanie nadal działa
        self.assertTrue(any((b.get("type") or "submit") == "submit" for b in form["buttons"]))

    def test_htmx_request_returns_fragment(self):
        r = self.client.post("/planner/calc/run/", CALC_DATA, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertNotIn("<html", body)
        self.assertNotIn("Błędne dane formularza", body)

    def test_plain_post_renders_full_page_with_result(self):
        """Bez htmx (tryb bez JS) — pełna strona kalkulatora z wynikiem w #result-area."""
        frag = self.client.post("/planner/calc/run/", CALC_DATA, HTTP_HX_REQUEST="true").content.decode()
        r = self.client.post("/planner/calc/run/", CALC_DATA)
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("<html", body)
        self.assertIn('id="manual-form"', body)
        self.assertIn('id="result-area"', body)
        self.assertNotIn("result-placeholder", body.split('id="result-area"', 1)[1][:400])
        self.assertTrue(frag.strip())
