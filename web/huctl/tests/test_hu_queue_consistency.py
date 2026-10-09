"""BIZ-004 / BIZ-008 (audyt): spójność kolejki kontroli HU.
- przydział lidera (hu_control_assign) jest rezerwacją — inny kontroler nie widzi
  i nie wywoła przydzielonej palety; zdjęcie przydziału zwalnia ją do puli,
- reopen świeżo startuje kontrolę — sweep porzuconych kontroli nie cofa jej od razu."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import HandlingUnit, Shipment
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


def _u(name, *groups):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class LeaderAssignReservesTests(TestCase):
    def setUp(self):
        self.c1 = _u("c1", GROUP_CONTROLLER)
        self.c2 = _u("c2", GROUP_CONTROLLER)
        self.leader = _u("lead", GROUP_LEADER)
        self.hu = HandlingUnit.objects.create(shipment=Shipment.objects.create(name="D1"),
                                              seq=1, code="H1", status="planned",
                                              warehouse_type="WT01")

    def _assign(self, uid):
        self.client.force_login(self.leader)
        self.client.post(reverse("ui:hu_control_assign", args=[self.hu.pk]), {"assignee": uid})
        self.hu.refresh_from_db()

    def _queue_pks(self, user):
        from huctl.views.hu_helpers import _call_queue
        req = RequestFactory().get("/")
        req.user = user
        req.session = {}
        return set(_call_queue(req).values_list("pk", flat=True))

    def test_assigned_hu_hidden_and_not_callable_by_other(self):
        self._assign(str(self.c1.pk))
        self.assertIsNotNone(self.hu.called_at)
        self.assertNotIn(self.hu.pk, self._queue_pks(self.c2))
        self.assertIn(self.hu.pk, self._queue_pks(self.c1))
        self.client.force_login(self.c2)
        self.client.post(reverse("ui:hu_call", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.assigned_to, self.c1)       # przydział lidera wygrywa

    def test_unassign_releases_to_pool(self):
        self._assign(str(self.c1.pk))
        self._assign("")
        self.assertIsNone(self.hu.assigned_to)
        self.assertIsNone(self.hu.called_at)
        self.assertIn(self.hu.pk, self._queue_pks(self.c2))


class ReopenRestartsControlTests(TestCase):
    def test_sweep_does_not_revert_reopened_hu(self):
        leader = _u("lead", GROUP_LEADER)
        hu = HandlingUnit.objects.create(shipment=Shipment.objects.create(name="D2"),
                                         seq=1, code="H2", status="ok",
                                         verified_at=timezone.now(),
                                         control_started_at=timezone.now() - timedelta(hours=6))
        self.client.force_login(leader)
        self.client.post(reverse("ui:hu_control_reopen", args=[hu.pk]), {"reason": "pomyłka"})
        from huctl.views.hu_control import _expire_stale_reservations
        cache.delete("hu_res_sweep")
        _expire_stale_reservations()
        hu.refresh_from_db()
        self.assertEqual(hu.status, "in_control")
        self.assertGreater(hu.control_started_at, timezone.now() - timedelta(minutes=5))
