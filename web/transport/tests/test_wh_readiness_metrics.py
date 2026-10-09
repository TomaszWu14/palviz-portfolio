# Fala 5: mail gotowości magazynu z wymiarami/wagami palet i sumami.
from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.models import HandlingUnit, QuoteRecipient, Shipment
from ui.roles import GROUP_TRANSPORT


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   EMAIL_HOST="smtp.test", DEFAULT_FROM_EMAIL="groove@test")
class ReadinessEmailMetricsTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_TRANSPORT)
        u = User.objects.create_user("tr", "tr@example.com", "Zx9!longpass")
        u.groups.add(g)
        self.client.force_login(u)
        QuoteRecipient.objects.create(name="Magazyn", email="wh@example.com",
                                      is_active=True, is_warehouse=True)
        self.sh = Shipment.objects.create(name="D-77")
        HandlingUnit.objects.create(shipment=self.sh, seq=1, code="HU-A", weight_kg=150,
                                    length_cm=120, width_cm=80, height_cm=100)
        HandlingUnit.objects.create(shipment=self.sh, seq=2, code="HU-B", weight_kg=50)

    def test_email_contains_hu_table_and_totals(self):
        r = self.client.post(reverse("ui:planner_shipment_wh_request",
                                     args=[self.sh.pk, "date"]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("HU-A", html)
        self.assertIn("120×80×100 cm", html)
        self.assertIn("150.0 kg", html.replace("150,0", "150.0"))
        self.assertIn("Razem", html)
        self.assertIn("200", html.replace(",", "."))   # suma wag 150+50
