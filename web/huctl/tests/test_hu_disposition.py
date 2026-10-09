"""Dyspozycja lidera „wyjechało bez kontroli" (escaped) — audyt BIZ-011.

1. HU w trakcie liczenia (in_control) wymaga JAWNEGO potwierdzenia (confirm_in_progress=1):
   bez niego brak zmiany + komunikat — lider nie „zwolni" przypadkiem palety, którą
   kontroler właśnie liczy (także gdy HU weszła w kontrolę po wyrenderowaniu panelu).
2. Po dyspozycji działa ta sama logika gotowości co przy finalize (notify_shipment_ready):
   gdy escaped była ostatnią nierozliczoną paletą, planiści dostają alert „gotowe"."""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import HandlingUnit, HUStatusEvent, Shipment
from ui.roles import GROUP_ADMIN, GROUP_LEADER

READY_TITLE = "Wszystkie palety gotowe"


def _user(name, *groups):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


def _msgs(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class DispositionInProgressTests(TestCase):
    """in_control = liczenie trwa → bez potwierdzenia odmowa, z potwierdzeniem escaped."""

    @classmethod
    def setUpTestData(cls):
        cls.leader = _user("lider-d", GROUP_LEADER)
        cls.ctrl = _user("kontroler-d")
        cls.sh = Shipment.objects.create(name="Dostawa BIZ011")

    def setUp(self):
        self.client.force_login(self.leader)
        self.hu = HandlingUnit.objects.create(
            shipment=self.sh, seq=1, code="BIZ011-1", status="in_control",
            controlled_by=self.ctrl, control_started_at=timezone.now())

    def _post(self, **extra):
        return self.client.post(reverse("ui:hu_control_disposition", args=[self.hu.pk]),
                                {"note": "zniknęła z SAP", **extra})

    def test_in_control_without_confirmation_is_refused(self):
        with mock.patch("ui.notifications.notify_shipment_ready") as ready:
            with self.captureOnCommitCallbacks(execute=True):
                r = self._post()
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "in_control")               # brak zmiany
        self.assertEqual(self.hu.controlled_by_id, self.ctrl.id)
        self.assertFalse(HUStatusEvent.objects.filter(hu=self.hu, to_status="escaped").exists())
        self.assertTrue(any("trwa liczenie" in m for m in _msgs(r)), _msgs(r))
        ready.assert_not_called()

    def test_in_control_with_confirmation_marks_escaped(self):
        self._post(confirm_in_progress="1")
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "escaped")
        ev = HUStatusEvent.objects.get(hu=self.hu, to_status="escaped")
        self.assertEqual(ev.from_status, "in_control")
        self.assertIn("przerwane liczenie", ev.note)                  # ślad audytu

    def test_planned_needs_no_confirmation(self):
        """Paleta nieliczona (planned) — dotychczasowe zachowanie bez zmian."""
        HandlingUnit.objects.filter(pk=self.hu.pk).update(
            status="planned", controlled_by=None, control_started_at=None)
        self._post()
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "escaped")

    def test_interrupted_controller_is_notified(self):
        """Liczący kontroler dostaje powiadomienie od razu (jak przy przejęciu), a nie
        dopiero błąd przy finalize, który dla 'escaped' jest zablokowany."""
        with mock.patch("ui.notifications.notify") as notify:
            self._post(confirm_in_progress="1")
        calls = [c for c in notify.call_args_list if "liczenie przerwane" in str(c)]
        self.assertEqual(len(calls), 1, notify.call_args_list)
        self.assertEqual([u.pk for u in calls[0].args[0]], [self.ctrl.pk])
        self.assertIn("lider-d", calls[0].kwargs["body"])

    def test_refused_or_planned_disposition_notifies_no_controller(self):
        with mock.patch("ui.notifications.notify") as notify:
            self._post()                                          # odmowa (brak potwierdzenia)
        self.assertFalse([c for c in notify.call_args_list if "liczenie przerwane" in str(c)])
        HandlingUnit.objects.filter(pk=self.hu.pk).update(
            status="planned", controlled_by=None, control_started_at=None)
        with mock.patch("ui.notifications.notify") as notify:
            self._post()
        self.assertFalse([c for c in notify.call_args_list if "liczenie przerwane" in str(c)])


class DispositionReadinessTests(TestCase):
    """Po dyspozycji ta sama logika gotowości co finalize: alert tylko gdy wszystko rozliczone."""

    @classmethod
    def setUpTestData(cls):
        cls.leader = _user("lider-r", GROUP_LEADER)

    def setUp(self):
        self.client.force_login(self.leader)
        self.sh = Shipment.objects.create(name="Dostawa gotowość")
        self.hu_ok = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="RD-1",
                                                 status="ok", verified_at=timezone.now())
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=2, code="RD-2")

    def _escape(self, hu):
        with self.captureOnCommitCallbacks(execute=True):          # notify idzie po commicie
            self.client.post(reverse("ui:hu_control_disposition", args=[hu.pk]))

    def _ready_alerts(self, notify):
        return [c for c in notify.call_args_list if READY_TITLE in str(c)]

    def test_disposition_runs_finalize_readiness_logic(self):
        with mock.patch("ui.notifications.notify_shipment_ready") as ready:
            self._escape(self.hu)
        ready.assert_called_once()
        self.assertEqual(ready.call_args.args[0].pk, self.sh.pk)

    def test_last_pallet_escaped_sends_ready_alert(self):
        with mock.patch("ui.notifications.notify") as notify:
            self._escape(self.hu)
        alerts = self._ready_alerts(notify)
        self.assertEqual(len(alerts), 1)
        # „wszystkie" = kontrolowane; paleta bez kontroli jest nazwana wprost.
        self.assertIn("wszystkie 1 palet", alerts[0].args[2])
        self.assertIn("Bez kontroli (dyspozycja lidera): 1 palet", alerts[0].args[2])
        self.sh.refresh_from_db()
        self.assertIsNotNone(self.sh.ready_notified_at)

    def test_ready_alert_without_escaped_keeps_message(self):
        from ui.notifications import notify_shipment_ready
        HandlingUnit.objects.filter(pk=self.hu.pk).update(status="ok", verified_at=timezone.now())
        with mock.patch("ui.notifications.notify") as notify:
            notify_shipment_ready(self.sh)
        alerts = self._ready_alerts(notify)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].args[2], "Skontrolowano OK wszystkie 2 palet — gotowe do wysyłki.")

    def test_not_all_settled_sends_no_ready_alert(self):
        HandlingUnit.objects.create(shipment=self.sh, seq=3, code="RD-3")   # nadal planned
        with mock.patch("ui.notifications.notify") as notify:
            self._escape(self.hu)
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "escaped")
        self.assertEqual(self._ready_alerts(notify), [])
        self.sh.refresh_from_db()
        self.assertIsNone(self.sh.ready_notified_at)


class DispositionHubFormTests(TestCase):
    """Panel: HU w kontroli dostaje jawne potwierdzenie (confirm() + confirm_in_progress),
    paleta planned — nie (jej zmiana w in_control po renderze zostanie odrzucona)."""

    def test_hub_form_carries_confirmation_only_for_in_control(self):
        leader = _user("lider-h", GROUP_ADMIN, GROUP_LEADER)
        ctrl = _user("kontrolerh")
        sh = Shipment.objects.create(name="Stock", is_stock=True)
        old = timezone.now() - timedelta(hours=48)
        HandlingUnit.objects.create(shipment=sh, seq=1, code="ST-PLAN", status="planned",
                                    last_seen_at=old)
        HandlingUnit.objects.create(shipment=sh, seq=2, code="ST-CTRL", status="in_control",
                                    controlled_by=ctrl, control_started_at=timezone.now(),
                                    last_seen_at=old)
        self.client.force_login(leader)
        r = self.client.get(reverse("ui:hu_control_hub"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="confirm_in_progress"', count=1)
        self.assertContains(r, "liczy ją kontrolerh")                  # kto liczy — w confirm()
