"""BIZ-005 (decyzja Q-42): JEDNA kolejka kontroli HU dla „Następna HU" i „Weź następną".

Ranking = „Weź następną" (VIP → PILNE → rekontrola → rodzina → SLA), te same filtry
w obu wejściach: snooze w przyszłości ukrywa HU, „dokończ swoją" (moja paleta w
kontroli / moja rezerwacja) idzie pierwsza. Przed zmianą „Następna HU" brała PILNE
przed VIP, a „Weź następną" ignorowała snooze i własną rezerwację/kontrolę."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from huctl.views.hu_dashboard import _queue
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_CONTROLLER


def _ctrl(name):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class OneQueueTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = _ctrl("ctrl")
        self.other = _ctrl("inny")
        self.today = timezone.localdate()
        self.client.force_login(self.u)

    def _hu(self, code, kunnr, vip=False, delivery=None, **kw):
        cust = Customer.objects.create(name=f"K{code}", kunnr=kunnr, is_vip=vip)
        sh = Shipment.objects.create(name=f"D{code}", customer=cust, kunnr=kunnr,
                                     outbound_delivery_date=delivery)
        kw.setdefault("status", "planned")
        return HandlingUnit.objects.create(shipment=sh, code=code, warehouse_type="92EX", **kw)

    def _mixed(self):
        """VIP, PILNE, rekontrola, rodzina w toku, 3× reszta (SLA bliski/daleki/brak)
        + HU odłożona (snooze) i HU zarezerwowana przez innego kontrolera."""
        self._hu("OLD", "300")                                          # reszta, bez terminu
        self._hu("FAR", "500", delivery=self.today + timedelta(days=10))
        self._hu("NEAR", "400", delivery=self.today + timedelta(days=1))
        self._hu("FAM", "200")
        self._hu("FAMDONE", "200", status="ok", verified_at=timezone.now())
        self._hu("RECH", "600", status="to_recheck")
        self._hu("PIL", "700", is_priority=True)
        self._hu("VIP", "100", vip=True)
        self._hu("SNOOZE", "101", vip=True, snooze_until=timezone.now() + timedelta(hours=4))
        self._hu("TAKEN", "102", vip=True, assigned_to=self.other, called_at=timezone.now())

    def _list(self, user=None):
        req = RequestFactory().get("/")
        req.user, req.session = user or self.u, {}
        return [h.code for h in _queue(req)[1]]

    def _picked(self, resp):
        self.assertEqual(resp.status_code, 302)
        pk = int(resp["Location"].rstrip("/").split("/")[-1])
        return HandlingUnit.objects.get(pk=pk)

    def _unreserve(self, hu):
        HandlingUnit.objects.filter(pk=hu.pk).update(assigned_to=None, called_at=None)

    EXPECTED = ["VIP", "PIL", "RECH", "FAM", "NEAR", "FAR", "OLD"]

    def test_queue_list_uses_take_next_ranking_and_filters(self):
        self._mixed()
        self.assertEqual(self._list(), self.EXPECTED)

    def test_next_hu_and_take_next_pick_same_order(self):
        self._mixed()
        seq = []
        for _ in self.EXPECTED:
            a = self._picked(self.client.get(reverse("ui:hu_control_next")))
            self._unreserve(a)
            b = self._picked(self.client.post(reverse("ui:hu_take_next")))
            self.assertEqual(a.code, b.code, f"rozjazd wejść po {seq}")
            seq.append(b.code)
            HandlingUnit.objects.filter(pk=b.pk).update(
                status="ok", verified_at=timezone.now(), assigned_to=None, called_at=None)
        self.assertEqual(seq, self.EXPECTED)

    def test_snoozed_hidden_from_both(self):
        snoozed = self._hu("SNZ", "100", vip=True,
                           snooze_until=timezone.now() + timedelta(hours=4))
        std = self._hu("STD", "200")
        self.assertNotIn(snoozed.code, self._list())
        a = self._picked(self.client.get(reverse("ui:hu_control_next")))
        self._unreserve(a)
        b = self._picked(self.client.post(reverse("ui:hu_take_next")))
        self.assertEqual((a.pk, b.pk), (std.pk, std.pk))

    def test_expired_snooze_back_in_queue(self):
        back = self._hu("BACK", "100", snooze_until=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self._list(), [back.code])

    def test_own_in_control_first_in_both(self):
        self._hu("VIP", "100", vip=True)
        mine = self._hu("MINE", "200", status="in_control", controlled_by=self.u,
                        control_started_at=timezone.now())
        for resp in (self.client.get(reverse("ui:hu_control_next")),
                     self.client.post(reverse("ui:hu_take_next"))):
            self.assertEqual(self._picked(resp).pk, mine.pk)
        self.assertIsNone(HandlingUnit.objects.get(code="VIP").assigned_to_id)

    def test_own_reservation_first_in_both(self):
        self._hu("VIP", "100", vip=True)
        mine = self._hu("RES", "200", assigned_to=self.u, called_at=timezone.now())
        self.assertEqual(self._list()[0], "RES")
        for resp in (self.client.get(reverse("ui:hu_control_next")),
                     self.client.post(reverse("ui:hu_take_next"))):
            self.assertEqual(self._picked(resp).pk, mine.pk)

    def test_my_shift_next_is_what_take_next_gives(self):
        self._hu("VIP", "100", vip=True)
        mine = self._hu("MINE", "200", status="in_control", controlled_by=self.u,
                        control_started_at=timezone.now())
        r = self.client.get(reverse("ui:hu_my_shift"))
        self.assertEqual(r.context["next_hu"].pk, mine.pk)
        self.assertEqual(r.context["next_badge"], "dokończ swoją")

    def test_reserved_by_other_hidden(self):
        self._hu("TAKEN", "100", vip=True, assigned_to=self.other, called_at=timezone.now())
        std = self._hu("STD", "200")
        self.assertEqual(self._list(), [std.code])
        self.assertEqual(self._list(self.other)[0], "TAKEN")
