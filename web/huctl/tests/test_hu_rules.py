"""BIZ-007: jedna definicja reguł Kontroli HU (huctl/rules.py).

- krótki termin: próg klienta (mies.), bez wymogu klienta 6 mies. — ta sama reguła
  w kolejce, przy księgowaniu i na karcie HU (czerwony = krótki termin, pomarańczowy
  < 12 mies.); wcześniej 183 dni / miesiące × 30,44 (z progiem 0 = wyłączone) / 183+365 dni,
- STATUS_GROUPS: `escaped` liczony wszędzie jako osobna grupa, nigdy praca otwarta,
- etykieta `escaped` z choices modelu („Wyjechało bez kontroli", nie „eskalacja"),
- przepustowość TV = unikalne pozycje jak kpi_stats (nie surowe próby)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from huctl import rules
from huctl.kpi import kpi_stats
from huctl.models import HUControlAttempt
from huctl.views.hu_helpers import _call_queue, _status_counts
from ui.models import Customer, HandlingUnit, HandlingUnitItem, Shipment
from ui.roles import GROUP_ADMIN, GROUP_CONTROLLER, GROUP_LEADER


def _user(name, *groups):
    u = get_user_model().objects.create_user(name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class ShortDatedRuleUnitTests(SimpleTestCase):
    today = timezone.localdate()

    def test_default_six_months_without_customer_requirement(self):
        for cust in (None, Customer(name="X"), Customer(name="Y", min_shelf_life_months=0)):
            self.assertEqual(rules.min_shelf_months(cust), 6)
        self.assertTrue(rules.is_short_dated(self.today + timedelta(days=182), None, self.today))
        self.assertFalse(rules.is_short_dated(self.today + timedelta(days=184), None, self.today))
        self.assertFalse(rules.is_short_dated(None, None, self.today))

    def test_customer_requirement_wins(self):
        c9 = Customer(name="K", min_shelf_life_months=9)
        self.assertTrue(rules.is_short_dated(self.today + timedelta(days=250), c9, self.today))
        c3 = Customer(name="K", min_shelf_life_months=3)
        self.assertFalse(rules.is_short_dated(self.today + timedelta(days=120), c3, self.today))

    def test_card_colours(self):
        d = lambda n: self.today + timedelta(days=n)
        self.assertEqual(rules.expiry_alert(d(100), None, self.today), "red")
        self.assertEqual(rules.expiry_alert(d(300), None, self.today), "orange")
        self.assertEqual(rules.expiry_alert(d(400), None, self.today), "")
        c12 = Customer(name="K", min_shelf_life_months=12)
        self.assertEqual(rules.expiry_alert(d(300), c12, self.today), "red")   # próg klienta
        self.assertEqual(rules.expiry_alert(None, None, self.today), "")

    def test_status_groups_escaped_own_group(self):
        self.assertNotIn("escaped", rules.STATUS_GROUPS["open"])
        self.assertEqual(rules.STATUS_GROUPS["escaped"], ("escaped",))
        self.assertEqual(set(rules.STATUSES), {s for s, _ in HandlingUnit.STATUS})
        self.assertEqual(rules.status_label("escaped"), "Wyjechało bez kontroli")


class ShortDatedSameEverywhereTests(TestCase):
    """Kolejka, księgowanie i karta HU dają ten sam werdykt dla tej samej pozycji."""

    def setUp(self):
        self.u = _user("c1", GROUP_CONTROLLER)
        self.client.force_login(self.u)
        self.today = timezone.localdate()

    def _hu(self, code, months=None, days=300, status="in_control"):
        cust = Customer.objects.create(name=f"K{code}", min_shelf_life_months=months)
        hu = HandlingUnit.objects.create(shipment=Shipment.objects.create(name=code, customer=cust),
                                         code=code, status=status, warehouse_type="92EX",
                                         controlled_by=self.u if status == "in_control" else None)
        HandlingUnitItem.objects.create(hu=hu, ref_code="A1", base_unit="OP", base_qty=1,
                                        expiry=self.today + timedelta(days=days))
        return hu

    def _queue_flags(self):
        req = RequestFactory().get("/")
        req.user, req.session = self.u, {}
        return {h.code: h.a_shortdated for h in _call_queue(req)}

    def test_queue_uses_customer_threshold(self):
        self._hu("K12", months=12, days=300, status="planned")   # < 12 mies. klienta
        self._hu("DEF", days=300, status="planned")              # > 6 mies. (domyślne)
        self._hu("DEF_SHORT", days=100, status="planned")        # < 6 mies.
        self.assertEqual(self._queue_flags(), {"K12": True, "DEF": False, "DEF_SHORT": True})

    def test_card_red_for_customer_threshold(self):
        hu = self._hu("K12", months=12, days=300)
        r = self.client.get(reverse("ui:hu_control_detail", args=[hu.pk]))
        self.assertEqual(r.context["items"][0].exp_alert, "red")   # dawniej 183 dni → orange
        self.assertContains(r, 'chip--exp exp-alert exp-alert--red')
        self.assertIn(hu.items.get(), r.context["short_dated"])

    def test_finalize_default_six_months_requires_ack(self):
        hu = self._hu("DEF", days=60)                            # klient bez wymogu
        it = hu.items.get()
        self.assertEqual(hu.short_dated_items(), [it])
        self.client.post(reverse("ui:hu_control_count", args=[hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": "1"})
        self.client.post(reverse("ui:hu_control_finalize", args=[hu.pk]))
        hu.refresh_from_db()
        self.assertNotEqual(hu.status, "ok")                     # krótki termin do potwierdzenia
        self.client.post(reverse("ui:hu_control_ack_short_dated", args=[hu.pk]))
        self.client.post(reverse("ui:hu_control_finalize", args=[hu.pk]))
        hu.refresh_from_db()
        self.assertEqual(hu.status, "ok")


class StatusGroupsAndThroughputTests(TestCase):
    def setUp(self):
        self.leader = _user("lead", GROUP_ADMIN, GROUP_LEADER)
        self.client.force_login(self.leader)
        sh = Shipment.objects.create(name="S")
        mk = lambda seq, status, **kw: HandlingUnit.objects.create(
            shipment=sh, seq=seq, code=f"H{seq}", status=status, warehouse_type="92EX", **kw)
        self.planned = mk(1, "planned")
        self.ok = mk(2, "ok", verified_at=timezone.now())
        self.escaped = mk(3, "escaped")

    def test_status_counts_escaped_own_group_not_open(self):
        c = _status_counts(HandlingUnit.objects.all())
        self.assertEqual((c["planned"], c["ok"], c["escaped"], c["open"]), (1, 1, 1, 1))

    def test_tv_and_hub_show_escaped(self):
        for url in ("ui:hu_control_tv", "ui:hu_control_hub"):
            r = self.client.get(reverse(url))
            self.assertEqual(r.context["status_counts"]["escaped"], 1)
            self.assertContains(r, "Wyjechało bez kontroli")

    def test_status_screen_progress_excludes_escaped_and_uses_model_label(self):
        r = self.client.get(reverse("ui:hu_control_status"))
        s = r.context["summary"]
        self.assertEqual((s["escaped"], s["done"], s["scope"], s["pct"]), (1, 1, 2, 50))
        self.assertContains(r, "Wyjechało bez kontroli")
        self.assertNotContains(r, "eskalacja")

    def test_tv_throughput_is_distinct_positions_like_kpi(self):
        it = HandlingUnitItem.objects.create(hu=self.planned, ref_code="A", base_qty=1)
        it2 = HandlingUnitItem.objects.create(hu=self.planned, ref_code="B", base_qty=1)
        for item in (it, it, it2):                  # edycja/rekontrola tej samej pozycji
            HUControlAttempt.objects.create(hu=self.planned, item=item, controller=self.leader,
                                            result="ok")
        now = timezone.now()
        _rows, totals = kpi_stats(now - timedelta(hours=1), now + timedelta(hours=1))
        r = self.client.get(reverse("ui:hu_control_tv"))
        self.assertEqual(r.context["today_positions"], 2)
        self.assertEqual(r.context["today_positions"], totals["positions"])
