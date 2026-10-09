"""Shipments bugfixy (code-review): SMS anti-pumping + walidacja telefonu na publicznym
driver_form; reminder pomija gdy brak SITE_BASE_URL (link bez hosta); render_cap nie
obcina generowania HU."""
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.models import Shipment, DriverAssignment
from ui.tasks import send_driver_reminders


class DriverSmsAntiPumpTests(TestCase):
    def setUp(self):
        self.sh = Shipment.objects.create(name="S1", destination_city="Kraków")
        self.da = DriverAssignment.objects.create(shipment=self.sh)

    def _post(self, phone):
        return self.client.post(reverse("ui:driver_form", args=[self.da.form_token]),
                                {"driver_name": "Jan", "driver_phone": phone})

    @override_settings(DRIVER_FORM_SMS_MAX=3)
    def test_sms_capped_despite_changing_numbers(self):
        for n in range(6):                       # 6 różnych numerów (próba pumpingu)
            self._post(f"+4860010020{n}")
        self.da.refresh_from_db()
        self.assertLessEqual(self.da.sms_count, 3)   # twardy cap, nie 6

    def test_invalid_phone_not_smsd(self):
        self._post("nie-numer")                  # niepoprawny E.164
        self.da.refresh_from_db()
        self.assertEqual(self.da.sms_count, 0)


class DriverReminderHostTests(TestCase):
    @override_settings(SITE_BASE_URL="")
    def test_reminder_skips_without_site_base_url(self):
        sh = Shipment.objects.create(name="S2")
        from django.utils import timezone
        DriverAssignment.objects.create(shipment=sh, driver_phone="+48600100200",
                                        pickup_status="pending", filled_at=timezone.now())
        res = send_driver_reminders()
        self.assertEqual(res["reminders_sent"], 0)   # pominięte — link byłby względny


class RenderCapTests(TestCase):
    def test_render_cap_none_returns_all_pallets(self):
        # Mały ładunek: render_cap=1 obcina rendered do 1, ale n_pallets pozostaje dokładny;
        # render_cap=None nie obcina (generowanie HU pokrywa każdą paletę).
        from ui.views.core.helpers import _build_shipment_three_data
        from ui.models import Product, PalletizationInstruction, Carton, ShipmentLine
        p = Product.objects.create(code="RC", name="X", unit_length_cm=20, unit_width_cm=15,
                                   unit_height_cm=10)
        c = Carton.objects.create(name="K", length_cm=40, width_cm=30, height_cm=25,
                                  unit_weight_kg=1, pieces_per_carton=10)
        PalletizationInstruction.objects.create(product=p, version=1, is_active=True,
            unit_weight=0.5, pcs_per_carton=10, carton=c, carton_l=40, carton_w=30, carton_h=25,
            pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15, max_height_total_cm=200)
        sh = Shipment.objects.create(name="RCship")
        ShipmentLine.objects.create(shipment=sh, product=p, quantity=300, unit="kar", source_unit="OP")
        from ui.views.core.helpers import _calc_shipment_data
        calc = _calc_shipment_data(sh)
        capped = _build_shipment_three_data(calc, render_cap=1)
        full = _build_shipment_three_data(calc, render_cap=None)
        self.assertEqual(len(capped["pallets"]), 1)                 # obcięte do 1
        self.assertEqual(len(full["pallets"]), full["n_pallets"])   # pełne = dokładny licznik
