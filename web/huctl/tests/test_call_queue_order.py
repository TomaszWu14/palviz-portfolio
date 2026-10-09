from datetime import date
from django.contrib.auth.models import Group, User
from django.test import TestCase, RequestFactory
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN
from huctl.views.hu_dashboard import _queue


class CallQueueOrderTests(TestCase):
    """Kolejność „Następna HU" = wspólna kolejka `_queue` (BIZ-005, Q-42): ranking
    „Weź następną" — VIP → PILNE → rekontrola → rodzina → SLA (→ krótki termin → FIFO)."""

    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u = User.objects.create_user("c", "c@e.pl", "Zx9!longpass")
        self.u.groups.add(g)  # admin omija strefy
        self.rf = RequestFactory()

    def _hu(self, name, cdate, status="planned", vip=False, prio=False):
        cust = Customer.objects.create(name=name, is_vip=vip)
        sh = Shipment.objects.create(name=name, customer=cust,
                                     outbound_created_date=cdate)
        return HandlingUnit.objects.create(shipment=sh, code=name,
                                           status=status, is_priority=prio)

    def _order(self):
        req = self.rf.get("/"); req.user = self.u; req.session = {}
        return [h.code for h in _queue(req)[1]]

    def test_vip_before_leader_priority(self):
        # Q-42: jeden ranking = „Weź następną" (VIP przed PILNE). Dawny SQL-owy ranking
        # „Następnej HU" stawiał is_priority przed VIP — stąd rozjazd dwóch wejść.
        self._hu("VIP", date(2026, 8, 1), vip=True)
        self._hu("PRIO", date(2026, 8, 3), prio=True)
        self.assertEqual(self._order(), ["VIP", "PRIO"])

    def test_recheck_before_planned(self):
        self._hu("PLAN", date(2026, 8, 1))
        self._hu("RECH", date(2026, 8, 3), status="to_recheck")
        self.assertEqual(self._order()[0], "RECH")

    def test_vip_then_fifo(self):
        self._hu("STD_OLD", date(2026, 8, 1))
        self._hu("VIP_NEW", date(2026, 8, 5), vip=True)
        self.assertEqual(self._order(), ["VIP_NEW", "STD_OLD"])

    def test_shortdated_before_fifo(self):
        from datetime import timedelta
        from ui.models import HandlingUnitItem
        from django.utils import timezone
        far = self._hu("FAR_OLD", date(2026, 8, 1))    # najstarsza, ale długi termin
        near = self._hu("NEAR_NEW", date(2026, 8, 9))  # nowsza, ale krótki termin
        HandlingUnitItem.objects.create(
            hu=far, ref_code="A1", expiry=timezone.localdate() + timedelta(days=400))
        HandlingUnitItem.objects.create(
            hu=near, ref_code="A2", expiry=timezone.localdate() + timedelta(days=30))
        self.assertEqual(self._order()[0], "NEAR_NEW")
