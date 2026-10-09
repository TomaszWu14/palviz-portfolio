"""Audyt INT-005: klucz GOOGLE_MAPS_API_KEY nie może opuścić serwera.

Mapa trasy (Static Maps) jest pobierana po stronie serwera — w e-mailu do przewoźnika
trafia jako obraz inline (multipart/related, ``cid:``), a na ekranie wyceny ładuje się
przez proxy ``ui:planner_shipment_route_map`` (ta sama rola co ekran wyceny). Sieć jest
zablokowana (testkit) — odpowiedzi Google pochodzą z FakeHTTP.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.roles import GROUP_MASTER_DATA, GROUP_TRANSPORT
from testkit.fake_http import FakeHTTP, Reply
from testkit.integrations import ROUTES
from ui.models import (
    PalletizationInstruction, Product, QuoteRecipient, Shipment, ShipmentLine,
)

KEY = "AIzaTEST-int005-sekretny-klucz"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64          # „obraz” — nikt go tu nie dekoduje
STATIC_RX = r"maps\.googleapis\.com/maps/api/staticmap"
PNG_OK = Reply(body=PNG, headers={"Content-Type": "image/png"})


def google(static=PNG_OK, directions_ok=True):
    """FakeHTTP z Directions (nagrana trasa) + Static Maps (``static``)."""
    fake = FakeHTTP()
    directions_rx, directions_ok_reply = ROUTES["google_maps"]
    fake.add(directions_rx, directions_ok_reply if directions_ok
             else Reply(status=503, body=b"Service Unavailable"))
    fake.add(STATIC_RX, static)
    return fake


def _static_calls(fake):
    return [c for c in fake.calls if "/maps/api/staticmap" in c.url]


def _user(username, *groups):
    u = get_user_model().objects.create_user(username=username, password="x",
                                              first_name="Jan", last_name="Kontakt",
                                              email=f"{username}@firma.pl")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


@override_settings(GOOGLE_MAPS_API_KEY=KEY, SHIPMENT_ORIGIN_ADDRESS="Radom, Polska",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MapsKeyLeakTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user("tr", GROUP_TRANSPORT)
        cls.sh = Shipment.objects.create(name="Dostawa INT-005", stowage_efficiency_pct=80,
                                         recipient_name="PHARMO", destination_city="VALDEMO",
                                         destination_country="IT")
        p = Product.objects.create(code="RG-50", name="RG")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=10, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=20, unit="kar")
        cls.rec = QuoteRecipient.objects.create(name="Speed A", email="a@speed.pl")

    def setUp(self):
        self.client.force_login(self.user)

    def _send(self, fake):
        with fake:
            resp = self.client.post(reverse("ui:planner_shipment_send_quote",
                                            args=[self.sh.pk, self.rec.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        return mail.outbox[0]

    def _assert_no_key(self, msg):
        self.assertNotIn(KEY, msg.body)
        for content, _mime in msg.alternatives:
            self.assertNotIn(KEY, content)
        self.assertNotIn(KEY, msg.message().as_string())
        self.assertNotIn("maps.googleapis.com", msg.alternatives[0][0])

    # ── e-mail do przewoźnika ────────────────────────────────────────────────────────────────
    def test_quote_email_embeds_map_inline_without_key(self):
        fake = google()
        m = self._send(fake)
        self._assert_no_key(m)
        # Mapę pobrał SERWER (to jedyne miejsce, gdzie klucz jest w użyciu).
        self.assertEqual(len(_static_calls(fake)), 1)
        # HTML odwołuje się do obrazu inline, obraz jest w części multipart/related.
        html = m.alternatives[0][0]
        self.assertIn('src="cid:', html)
        root = m.message()
        self.assertEqual(root.get_content_type(), "multipart/related")
        images = [p for p in root.walk() if p.get_content_type() == "image/png"]
        self.assertEqual(len(images), 1)
        cid = images[0]["Content-ID"].strip("<>")
        self.assertIn(f'src="cid:{cid}"', html)
        self.assertEqual(images[0].get_payload(decode=True), PNG)
        self.assertTrue(images[0]["Content-Disposition"].startswith("inline"))

    def test_quote_email_sent_without_map_when_static_fetch_fails(self):
        for reply in (Reply(raises="timeout"), Reply(raises="connection"),
                      Reply(status=403, body=b"The provided API key is invalid."),
                      Reply(status=200, body=b"<html>proxy</html>", headers={"Content-Type": "text/html"})):
            with self.subTest(status=reply.status, raises=reply.raises):
                mail.outbox = []
                with self.assertLogs("transport.views.mailing", level="WARNING") as logs:
                    m = self._send(google(static=reply))
                self._assert_no_key(m)
                self.assertNotIn("cid:", m.alternatives[0][0])
                self.assertNotIn("<img", m.alternatives[0][0])
                self.assertEqual(m.attachments, [])
                # Ostrzeżenie w logu — bez klucza i bez URL-a, który go zawiera.
                self.assertNotIn(KEY, "\n".join(logs.output))
                self.assertNotIn("staticmap?", "\n".join(logs.output))

    def test_quote_email_without_route_skips_static_fetch(self):
        fake = google(directions_ok=False)
        m = self._send(fake)
        self._assert_no_key(m)
        self.assertEqual(_static_calls(fake), [])
        self.assertNotIn("<img", m.alternatives[0][0])

    # ── ekran wyceny w przeglądarce + mailto (fallback bez SMTP) ─────────────────────────────
    def test_quote_screen_uses_proxy_not_keyed_url(self):
        fake = google()
        with fake:
            resp = self.client.get(reverse("ui:planner_shipment_quote", args=[self.sh.pk]))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertNotIn(KEY, html)
        self.assertNotIn("maps.googleapis.com/maps/api/staticmap", html)
        self.assertIn(reverse("ui:planner_shipment_route_map", args=[self.sh.pk]), html)
        # Obrazu nie pobieramy przy renderze ekranu — robi to dopiero proxy.
        self.assertEqual(_static_calls(fake), [])
        for r in resp.context["recipients"]:
            self.assertNotIn(KEY, r["mailto"])

    # ── proxy mapy trasy ─────────────────────────────────────────────────────────────────────
    def test_route_map_proxy_returns_png_without_key(self):
        fake = google()
        url = reverse("ui:planner_shipment_route_map", args=[self.sh.pk])
        with fake:
            resp = self.client.get(url, {"origin": "Radom"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertEqual(resp.content, PNG)
        self.assertIn("private", resp["Cache-Control"])
        self.assertNotIn(KEY, str(resp.headers))
        # Start trasy z parametru ?origin= (jak na ekranie wyceny).
        self.assertIn("origin=Radom", fake.calls[0].url)

    def test_route_map_proxy_404_when_fetch_fails(self):
        url = reverse("ui:planner_shipment_route_map", args=[self.sh.pk])
        with self.assertLogs("transport.views.mailing", level="WARNING"):
            with google(static=Reply(status=403, body=b"denied")):
                resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(KEY.encode(), resp.content)
        self.assertNotIn(KEY, str(resp.headers))

    def test_route_map_proxy_requires_login_and_transport_role(self):
        url = reverse("ui:planner_shipment_route_map", args=[self.sh.pk])
        fake = google()
        with fake:
            anon = Client().get(url)
            c = Client()
            c.force_login(_user("md", GROUP_MASTER_DATA))
            no_role = c.get(url)
        self.assertEqual(anon.status_code, 302)
        self.assertIn("/login/", anon["Location"])
        self.assertEqual(no_role.status_code, 403)
        self.assertEqual(fake.calls, [])                 # bez uprawnień — zero wywołań Google
        for resp in (anon, no_role):
            self.assertNotIn(KEY.encode(), resp.content)
