"""Spec wywołań — audyt: zdarzenia kind=call/release, czas wywołanie→start policzalny
z HUStatusEvent + control_started_at, oraz piny liczby zapytań (assertNumQueries)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Shipment, HandlingUnit, HandlingUnitItem, HUStatusEvent
from ui.roles import GROUP_CONTROLLER


def _controller(name="ctrl"):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class CallEventKinds(TestCase):
    def setUp(self):
        self.u = _controller()
        self.sh = Shipment.objects.create(name="D")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="M1",
                                              status="planned", is_completed=True)
        self.client.force_login(self.u)

    def test_call_logs_kind_call(self):
        self.client.post(reverse("ui:hu_call", args=[self.hu.pk]))
        ev = HUStatusEvent.objects.get(hu=self.hu)
        self.assertEqual(ev.kind, "call")
        self.assertEqual(ev.from_status, ev.to_status)          # status niezmieniony

    def test_release_logs_kind_release_with_reason(self):
        self.client.post(reverse("ui:hu_call", args=[self.hu.pk]))
        self.client.post(reverse("ui:hu_release", args=[self.hu.pk]),
                         {"reason": "wózek zajęty"})
        ev = HUStatusEvent.objects.filter(hu=self.hu, kind="release").get()
        self.assertIn("wózek zajęty", ev.note)

    def test_call_to_start_time_measurable(self):
        """Metryka „czas wywołanie→start”: zdarzenie call + control_started_at dają różnicę."""
        self.client.post(reverse("ui:hu_call", args=[self.hu.pk]))
        self.client.post(reverse("ui:hu_control_start", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        call_ev = HUStatusEvent.objects.filter(hu=self.hu, kind="call").earliest("created_at")
        self.assertIsNotNone(self.hu.control_started_at)
        self.assertGreaterEqual((self.hu.control_started_at - call_ev.created_at)
                                .total_seconds(), 0)

    def test_next_returns_active_hu_first(self):
        """Bilans obciążenia: kontroler z paletą in_control wraca do niej zamiast brać
        kolejne czoło kolejki."""
        active = HandlingUnit.objects.create(shipment=self.sh, seq=2, code="M2",
                                             status="in_control", controlled_by=self.u)
        r = self.client.get(reverse("ui:hu_control_next"))
        self.assertRedirects(r, reverse("ui:hu_control_detail", args=[active.pk]))


class QueryPins(TestCase):
    """Regresja wydajności (spec): stała liczba zapytań na liście HU i „następnej”."""

    @classmethod
    def setUpTestData(cls):
        cls.u = get_user_model().objects.create_user("adm", password="x", is_superuser=True)
        for i in range(30):
            sh = Shipment.objects.create(name=f"D{i}")
            hu = HandlingUnit.objects.create(shipment=sh, seq=1, code=f"Q{i}",
                                             is_completed=True)
            HandlingUnitItem.objects.create(hu=hu, ref_code=f"R{i}", base_qty=1,
                                            base_unit="OP")

    def test_hu_list_query_count_constant(self):
        self.client.force_login(self.u)
        self.client.get(reverse("ui:planner_stock_contents"), {"view": "hu"})  # warm-up sesji
        with self.assertNumQueries(FuzzyInt(5, 30)):
            self.client.get(reverse("ui:planner_stock_contents"), {"view": "hu"})

    def test_next_query_count_constant(self):
        ctrl = _controller("qc")
        self.client.force_login(ctrl)
        self.client.get(reverse("ui:hu_control_next"))          # warm-up
        with self.assertNumQueries(FuzzyInt(5, 30)):
            self.client.get(reverse("ui:hu_control_next"))


class FuzzyInt(int):
    """assertNumQueries z przedziałem: pinuje górną granicę (N+1 przy 30 wierszach by ją
    przebiło), nie wymuszając kruchej dokładnej liczby przy każdej zmianie middleware."""
    def __new__(cls, lowest, highest):
        obj = super().__new__(cls, highest)
        obj.lowest, obj.highest = lowest, highest
        return obj

    def __eq__(self, other):
        return self.lowest <= other <= self.highest

    def __hash__(self):
        return int.__hash__(self)
