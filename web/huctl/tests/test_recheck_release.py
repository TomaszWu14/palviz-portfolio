"""Przejście do rekontroli zwalnia rezerwację liczącego kontrolera.

Rekontrolę musi zrobić INNY kontroler, a kolejka (`_call_queue`) pokazuje innym tylko palety
bez aktywnej rezerwacji. Sweep porzuconych rezerwacji sprząta wyłącznie „planned”, więc HU
w to_recheck z rezerwacją (assigned_to + called_at z „Weź następną”/wywołania) wisiała
u kontrolera, który nie może jej przeliczyć — dla pozostałych była niewidoczna."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from huctl.views.hu import _generate_handling_units
from huctl.views.hu_helpers import _call_queue
from ui.models import PalletizationInstruction, Product, Shipment, ShipmentLine
from ui.roles import ALL_GROUPS


def _user(name):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class RecheckReleasesReservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.c1 = _user("liczacy")
        cls.c2 = _user("rekontroler")
        sh = Shipment.objects.create(name="Dostawa rekontrola")
        p = Product.objects.create(code="RR-1", name="Towar")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=10, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(shipment=sh, product=p, quantity=8, unit="kar", source_unit="OP")
        _generate_handling_units(sh)
        cls.hu = sh.handling_units.first()

    def setUp(self):
        self.client.force_login(self.c1)
        # Paleta wzięta przez „Weź następną” — rezerwacja c1.
        type(self.hu).objects.filter(pk=self.hu.pk).update(assigned_to=self.c1,
                                                           called_at=timezone.now())
        self.client.post(reverse("ui:hu_control_start", args=[self.hu.pk]))

    def _queue_for(self, user):
        req = RequestFactory().get("/")
        req.user, req.session = user, {}
        return [h.pk for h in _call_queue(req)]

    def _finalize(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()

    def test_discrepancy_releases_reservation_for_other_controllers(self):
        it = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "sure": "1", "qty_base": str(it.base_qty - 1)})
        self._finalize()
        self.assertEqual(self.hu.status, "to_recheck")
        self.assertIsNone(self.hu.assigned_to_id)
        self.assertIsNone(self.hu.called_at)
        self.assertIn(self.hu.pk, self._queue_for(self.c2))

    def test_expired_lot_releases_reservation(self):
        it = self.hu.items.first()
        it.expiry = timezone.localdate() - timedelta(days=1)
        it.save(update_fields=["expiry"])
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty)})
        self._finalize()
        self.assertEqual(self.hu.status, "to_recheck")
        self.assertIsNone(self.hu.assigned_to_id)
        self.assertIsNone(self.hu.called_at)

    def test_ok_finalize_unchanged(self):
        it = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty)})
        self._finalize()
        self.assertEqual(self.hu.status, "ok")
