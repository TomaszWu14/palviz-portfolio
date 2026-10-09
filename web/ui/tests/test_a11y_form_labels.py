"""Etap 5 audytu UX: każde pole formularza ma dostępną nazwę (axe `label` / `select-name`).

Pole jest nazwane (reguły axe), gdy ma aria-label / aria-labelledby / title / placeholder,
siedzi wewnątrz <label> albo jakiś <label for> wskazuje jego id. Widoki z naruszeniami po etapie 4 + macierze (5b) + formularze (5c), dane z seeda.
"""

from html.parser import HTMLParser

from django.test import TestCase

from testkit.personas import client_for
from testkit.seed import SeedDataMixin

SKIP_TYPES = {"hidden", "submit", "button", "reset", "image"}


class _Fields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields, self.label_for, self.in_label = [], set(), 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self.in_label += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        elif tag in ("input", "select", "textarea") and a.get("type", "text") not in SKIP_TYPES:
            named = bool(a.get("aria-label") or a.get("aria-labelledby") or a.get("title")
                         or a.get("placeholder") or self.in_label)  # jak axe
            self.fields.append((a.get("id"), a.get("name"), named))

    def handle_endtag(self, tag):
        if tag == "label" and self.in_label:
            self.in_label -= 1

    def unnamed(self):
        return [(i, n) for i, n, named in self.fields if not named and i not in self.label_for]


class FormLabelsTests(SeedDataMixin, TestCase):
    URLS = ["/data-center/", "/planner/calc/", "/zaria/",
            "/data-center/packaging/", "/planner/cartons/unify/", "/control/leader/",
            # 5c — formularze i filtry (widoki bez <pk>)
            "/magazyn/editor3d/", "/magazyn/rack-generator/", "/magazyn/types/new/",
            "/magazyn/lokalizacje/master/", "/magazyn/warianty/", "/magazyn/heatmapa/",
            "/planner/products/new/", "/planner/cartons/new/", "/planner/shipments/new/",
            "/planner/optimizer/", "/planner/quote-recipients/", "/planner/ref-materials/",
            "/ukraina/", "/ukraina/linia/new/", "/tasks/", "/tasks/form/", "/profil/",
            "/control/symulator/", "/control/report/", "/control/recipient/",
            "/optymalizacja/pilnosc/", "/optymalizacja/ab/", "/stock-warehouse/contents/",
            "/data-center/matrix/", "/wymiary-producenta/", "/phv/moje/", "/zgloszenia/"]

    def test_every_field_has_accessible_name(self):
        client = client_for("superuser")
        for url in self.URLS:
            with self.subTest(url=url):
                r = client.get(url)
                self.assertEqual(r.status_code, 200)
                p = _Fields()
                p.feed(r.content.decode())
                self.assertTrue(p.fields, "brak pól — zły widok?")
                self.assertEqual(p.unnamed(), [])
