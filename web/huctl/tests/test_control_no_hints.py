"""Ekran liczenia nie podpowiada spodziewanej ilości.

Suma jednostek / waga / objętość HU sugerowały kontrolerowi wynik, przez co kontrola
zamieniała się w potwierdzanie podpowiedzi zamiast liczenia. Te liczby zostają na liście
HU i w panelu lidera, ale nie na ekranie pozycji."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Shipment, HandlingUnit, HandlingUnitItem
from ui.roles import GROUP_CONTROLLER


def _controller(name="kontroler"):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class CountingScreenHasNoQuantityHints(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _controller()
        sh = Shipment.objects.create(name="Dostawa", is_stock=True)
        cls.hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HU-NOHINT",
                                             location="23L.04", warehouse_type="92JU")
        HandlingUnitItem.objects.create(hu=cls.hu, ref_code="DMOM10001",
                                        description="Rękawice", base_unit="OP", base_qty=130,
                                        alt_unit="KAR", alt_qty=13)

    def setUp(self):
        self.client.force_login(self.user)

    def test_header_has_no_sum_weight_or_volume(self):
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertNotIn("Σ ", body)          # suma jednostek pobrania
        self.assertNotIn("⚖", body)           # waga HU
        self.assertNotIn("◫", body)           # objętość HU
        self.assertNotIn("units_summary", r.context)
        self.assertNotIn("hu_metric", r.context)

    def test_retired_error_flags_not_offered(self):
        # Soft-delete: „Błąd na etykiecie" i „Brak dokumentu / certyfikatu" wygaszone —
        # nie pojawiają się już w UI wejścia kontrolera (historia renderuje je osobno).
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertNotContains(r, "Błąd na etykiecie")
        self.assertNotContains(r, "Brak dokumentu / certyfikatu")
        self.assertNotContains(r, "flag_wrong_label")
        self.assertContains(r, "Uszkodzony towar")          # aktywny kod nadal jest

    def test_pick_hint_shows_unit_without_quantity(self):
        """Podpowiedź z logu pobrań mówi W CZYM pobierano, ale nie ILE."""
        from datetime import timedelta
        from django.utils import timezone
        from ui.models import PickerActivity, PickerActivityBatch
        batch = PickerActivityBatch.objects.create(name="feed")
        PickerActivity.objects.create(batch=batch, material_code="DMOM10001", qty=13,
                                      unit="KAR", location_code="23L.04",
                                      confirmed_at=timezone.now() - timedelta(hours=2))
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        item = r.context["items"][0]
        self.assertEqual(item.picked_label, "KAR")     # sama jednostka
        self.assertNotIn("13", item.picked_label)      # bez ilości
        self.assertIn("kar", item.picked_units)        # kafel nadal podświetlony

    def test_vendor_batch_shown_when_present(self):
        it = self.hu.items.first()
        it.vendor_batch = "1000195047"
        it.save(update_fields=["vendor_batch"])
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertContains(r, "1000195047")
        self.assertContains(r, "PRODUCENT")
