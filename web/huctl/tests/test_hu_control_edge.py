"""Kontrola HU — przypadki BRZEGOWE najbardziej ryzykownych operacji (ticket 09,
.scratch/concerns-cleanup): stale HU (domknięta między odczytem a akcją), współbieżne
picki (dwóch kontrolerów o tę samą paletę), przejęcia kontroli/rezerwacji.
Dotąd pokryte były głównie happy-paths; te testy pilnują ścieżek błędnych."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import (Product, Shipment, ShipmentLine, PalletizationInstruction,
                       HandlingUnit)
from ui.roles import ALL_GROUPS
# Po wydzieleniu apki huctl funkcja mieszka w huctl.views.hu (nazwa z podkreśleniem
# nie przechodzi przez star-export agregatora ui.views).
from huctl.views.hu import _generate_handling_units


def _controller(name="ctrl1"):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


def _shipment_with_hu(name="Dostawa E", code="EDG-1"):
    sh = Shipment.objects.create(name=name, stowage_efficiency_pct=80)
    p = Product.objects.create(code=code, name="Materiał edge")
    PalletizationInstruction.objects.create(
        product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
        unit_weight=0.5, pcs_per_carton=10, demand_pcs=100, is_active=True)
    ShipmentLine.objects.create(shipment=sh, product=p, quantity=8, unit="kar",
                                source_unit="OP")
    _generate_handling_units(sh)
    return sh, sh.handling_units.first()


class StaleHUTests(TestCase):
    """HU domknięta (ok/escaped) nie daje się ruszyć żadną akcją operacyjną."""

    @classmethod
    def setUpTestData(cls):
        cls.u = _controller()
        cls.sh, cls.hu = _shipment_with_hu()

    def setUp(self):
        self.client.force_login(self.u)

    def test_start_on_verified_hu_is_noop(self):
        HandlingUnit.objects.filter(pk=self.hu.pk).update(status="ok", verified_at=timezone.now())
        self.client.post(reverse("ui:hu_control_start", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "ok")               # nie wraca do in_control

    def test_takeover_on_closed_hu_is_noop(self):
        HandlingUnit.objects.filter(pk=self.hu.pk).update(status="escaped")
        self.client.post(reverse("ui:hu_control_takeover", args=[self.hu.pk]),
                         {"reason": "test"})
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "escaped")
        self.assertIsNone(self.hu.controlled_by)

    def test_escape_on_already_verified_hu_is_noop(self):
        """Dyspozycja „wyjechało bez kontroli" nie nadpisuje już zweryfikowanej HU."""
        HandlingUnit.objects.filter(pk=self.hu.pk).update(status="ok", verified_at=timezone.now())
        self.client.post(reverse("ui:hu_control_disposition", args=[self.hu.pk]),
                         {"note": "próba"})
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "ok")


class ConcurrentPickTests(TestCase):
    """Dwóch kontrolerów o tę samą paletę: pierwszy wygrywa, drugi jest jasno odbity."""

    @classmethod
    def setUpTestData(cls):
        cls.a = _controller("ctrl-a")
        cls.b = _controller("ctrl-b")
        cls.sh, cls.hu = _shipment_with_hu("Dostawa C", "EDG-2")

    def test_second_call_does_not_steal_reservation(self):
        self.client.force_login(self.a)
        self.client.post(reverse("ui:hu_call", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.assigned_to_id, self.a.id)
        # Kontroler B próbuje wywołać tę samą paletę — rezerwacja zostaje przy A.
        self.client.force_login(self.b)
        self.client.post(reverse("ui:hu_call", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.assigned_to_id, self.a.id)

    def test_reserve_takeover_moves_planned_reservation(self):
        """Miękkie przejęcie REZERWACJI (planned): świadomy POST przenosi na B."""
        self.client.force_login(self.a)
        self.client.post(reverse("ui:hu_call", args=[self.hu.pk]))
        self.client.force_login(self.b)
        self.client.post(reverse("ui:hu_reserve_takeover", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "planned")           # wciąż nieliczona
        self.assertEqual(self.hu.assigned_to_id, self.b.id)

    def test_reserve_takeover_refused_when_in_control(self):
        """Paleta już LICZONA (in_control przez A): przejęcie rezerwacji odbite —
        wymaga jawnego „Przejmij kontrolę" z powodem."""
        self.client.force_login(self.a)
        self.client.post(reverse("ui:hu_control_start", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "in_control")
        self.client.force_login(self.b)
        self.client.post(reverse("ui:hu_reserve_takeover", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.controlled_by_id, self.a.id)  # kontrola zostaje przy A

    def test_control_takeover_transfers_with_audit(self):
        """Twarde przejęcie KONTROLI: przenosi controlled_by i rezerwację na B."""
        self.client.force_login(self.a)
        self.client.post(reverse("ui:hu_control_start", args=[self.hu.pk]))
        self.client.force_login(self.b)
        self.client.post(reverse("ui:hu_control_takeover", args=[self.hu.pk]),
                         {"reason": "A poszedł na przerwę"})
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.controlled_by_id, self.b.id)
        self.assertEqual(self.hu.assigned_to_id, self.b.id)   # rezerwacja podąża za kontrolą
        self.assertEqual(self.hu.status, "in_control")

    def test_next_returns_active_pallet_before_new_one(self):
        """Kontroler z aktywną paletą dostaje JĄ z powrotem, nie kolejną z kolejki."""
        self.client.force_login(self.a)
        self.client.post(reverse("ui:hu_control_start", args=[self.hu.pk]))
        resp = self.client.get(reverse("ui:hu_control_next"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"/{self.hu.pk}/", resp["Location"])
