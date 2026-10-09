"""Testy regresji poprawek importu przesyłek (``planner_shipments_import``).

1. Wykrywanie kolumn niezależne od kolejności nagłówków (dokładne > słowo > podciąg,
   nagłówek przypisany do jednego pola nie jest brany do innego).
2. Numer odbiorcy ze spacją nie ląduje jako nazwa odbiorcy.
3. Kod kraju ISO-3 mapowany na ISO-2 (nieznany → pusty + komunikat), nie ucinany.
4. Nazwa „Dostawa <dokument>” przycinana do max_length pola ``Shipment.name``.
5. Komunikaty: bez podwójnej spacji, sparowane cudzysłowy.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from transport.models_shipment import Shipment
from transport.views import shipments_import_parse as parse
from ui.models import Product
from ui.roles import GROUP_TRANSPORT


def _csv(headers, rows):
    lines = [";".join(headers)] + [";".join(str(c) for c in r) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _hdr(*names):
    return [n.strip().lower() for n in names]


class DetectColumnsTests(SimpleTestCase):
    def test_postal_before_product(self):
        idx = parse.detect_columns(_hdr("Dokument", "Kod pocztowy", "Produkt", "Ilość", "JS"))
        self.assertEqual((idx["doc"], idx["postal"], idx["prod"], idx["qty"], idx["unit"]),
                         (0, 1, 2, 3, 4))

    def test_hu_quantity_before_quantity(self):
        idx = parse.detect_columns(_hdr("Dokument", "Produkt", "Ilość HO", "Ilość", "JS"))
        self.assertEqual((idx["hu"], idx["qty"]), (2, 3))

    def test_header_order_does_not_matter(self):
        names = ["Dokument", "Produkt", "Ilość", "JS", "Kod pocztowy", "Ilość HO",
                 "Nr odbiorcy", "Nazwa odbiorcy", "Miasto", "Kraj"]
        base = parse.detect_columns(_hdr(*names))
        rev = parse.detect_columns(_hdr(*reversed(names)))
        n = len(names)
        for key, i in base.items():
            self.assertEqual(rev[key], None if i is None else n - 1 - i, key)

    def test_sap_layout_unchanged(self):
        hdr = _hdr("Dokument", "Produkt", "Ilość", "Jednostka miary", "Dost.Odbiorca materiałów",
                   "Dost.Opis odbiorcy materiałów", "Miejscowość", "Dost.Kod pocztowy",
                   "Dost.Klucz kraju/regionu", "Dost.Autor", "Mail", "Wymagania klienta",
                   "Dost.Liczba jednostek obsługi")
        idx = parse.detect_columns(hdr)
        self.assertEqual(
            {k: idx[k] for k in ("doc", "prod", "qty", "unit", "recip_no", "recip_name",
                                 "city", "postal", "country", "author", "mail", "req", "hu")},
            {"doc": 0, "prod": 1, "qty": 2, "unit": 3, "recip_no": 4, "recip_name": 5,
             "city": 6, "postal": 7, "country": 8, "author": 9, "mail": 10, "req": 11,
             "hu": 12})

    def test_positional_fallback(self):
        idx = parse.detect_columns(_hdr("A", "B", "C", "D"))
        self.assertEqual((idx["doc"], idx["prod"], idx["qty"], idx["unit"]), (0, 1, 2, 3))


class ParseHelpersTests(SimpleTestCase):
    def test_recipient_number_with_space_not_stored_as_name(self):
        rs = [{"recip_no": "AB 12", "recip_name": "AB 12", "recipient": ""}]
        self.assertEqual(parse.recipient_fields(rs), ("", ""))

    def test_country_codes(self):
        self.assertEqual(parse.country_code("AUT"), ("AT", None))
        self.assertEqual(parse.country_code("deu"), ("DE", None))
        self.assertEqual(parse.country_code("gbr"), ("GB", None))
        self.assertEqual(parse.country_code("UKR"), ("UA", None))
        self.assertEqual(parse.country_code("pl"), ("PL", None))
        self.assertEqual(parse.country_code(""), ("", None))
        self.assertEqual(parse.country_code("XYZ"), ("", "XYZ"))

    def test_long_document_name_truncated(self):
        rs = [{"doc": "D" * 300}]
        name, _ = parse.shipment_name_notes(rs, False, "", "", max_len=200)
        self.assertEqual(len(name), 200)
        self.assertTrue(name.startswith("Dostawa DDD"))

    def test_summary_message_no_double_space(self):
        stats = {"shipments": 1, "lines": 2, "linked": 0, "created_customers": 0}
        msg = parse.summary_message(stats, False, 1, set())
        self.assertNotIn("  ", msg)
        self.assertIn("Zaimportowano 1 przesyłek (2 linii)", msg)
        self.assertIn("przesyłek skonsolidowanych (2 linii)",
                      parse.summary_message(stats, True, 1, set()))


class ShipmentsImportFixesViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Product.objects.create(code="P1", name="Produkt P1")

    def setUp(self):
        u = get_user_model().objects.create_user(username="ship-fix")
        u.groups.add(Group.objects.get_or_create(name=GROUP_TRANSPORT)[0])
        self.client.force_login(u)

    def _post(self, *files):
        ups = [SimpleUploadedFile(n, b, content_type="text/csv") for n, b in files]
        resp = self.client.post(reverse("ui:planner_shipments_import"), {"file": ups})
        return [m.message for m in get_messages(resp.wsgi_request)]

    def test_postal_before_product_imports(self):
        self._post(("g.csv", _csv(["Dokument", "Kod pocztowy", "Produkt", "Ilość", "JS"],
                                  [["G1", "00-001", "P1", "5", "KAR"]])))
        s = Shipment.objects.get()
        self.assertEqual((s.destination_postal, s.lines.get().quantity), ("00-001", 5))

    def test_iso3_country_mapped_and_unknown_reported(self):
        msgs = self._post(("c.csv", _csv(["Dokument", "Produkt", "Ilość", "JS", "Kraj"],
                                         [["A1", "P1", "1", "KAR", "AUT"],
                                          ["A2", "P1", "1", "KAR", "XYZ"]])))
        by_name = dict(Shipment.objects.values_list("name", "destination_country"))
        self.assertEqual(by_name, {"Dostawa A1": "AT", "Dostawa A2": ""})
        self.assertIn("XYZ", msgs[0])

    def test_long_document_number_name_fits_max_length(self):
        self._post(("l.csv", _csv(["Dokument", "Produkt", "Ilość", "JS"],
                                  [["D" * 300, "P1", "1", "KAR"]])))
        max_len = Shipment._meta.get_field("name").max_length
        self.assertEqual(len(Shipment.objects.get().name), max_len)

    def test_too_large_message_quotes_paired(self):
        msgs = self._post(("big.csv", b"x" * (10 * 1024 * 1024 + 1)))
        self.assertEqual(msgs, ["Plik „big.csv” zbyt duży (max 10 MB)."])
