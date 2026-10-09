"""Pulpit „Moja zmiana": ranking VIP → PILNE → rekontrole → dokończenie rodzin →
najstarsze + „Weź następną" (rezerwacja pierwszej wg rankingu)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from huctl.views.hu_dashboard import families_in_progress, rank_key
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_CONTROLLER


class MyShiftTests(TestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_user("ctrl", password="x")
        self.u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        vip_c = Customer.objects.create(name="VIPCO", kunnr="100", is_vip=True)
        std_c = Customer.objects.create(name="STD", kunnr="200")
        self.sh_vip = Shipment.objects.create(name="DV", customer=vip_c, kunnr="100")
        self.sh_std = Shipment.objects.create(name="DS", customer=std_c, kunnr="200")
        mk = lambda seq, sh, **kw: HandlingUnit.objects.create(
            shipment=sh, seq=seq, code=f"H{seq}", warehouse_type="92EX", **kw)
        self.old = mk(1, self.sh_std)                       # najstarsza STANDARD
        self.rec = mk(2, self.sh_std, status="to_recheck")  # rekontrola
        self.pil = mk(3, self.sh_std, is_priority=True)     # PILNE
        self.vip = mk(4, self.sh_vip)                       # VIP
        self.client.force_login(self.u)

    def _ranked(self):
        hus = list(HandlingUnit.objects.select_related("shipment__customer"))
        fam = families_in_progress(hus)
        return sorted(hus, key=lambda h: rank_key(h, fam))

    def test_ranking_vip_first_then_pilne_then_recheck(self):
        order = [h.pk for h in self._ranked()]
        self.assertEqual(order[:3], [self.vip.pk, self.pil.pk, self.rec.pk])

    def test_family_in_progress_before_oldest(self):
        # klient 200 „rozgrzebany": jedna HU skontrolowana → jego planned przed obcymi.
        other = Shipment.objects.create(name="DX", kunnr="300")
        oldest_other = HandlingUnit.objects.create(
            shipment=other, seq=9, code="H9", warehouse_type="92EX")
        from django.utils import timezone
        HandlingUnit.objects.filter(pk=self.rec.pk).update(
            status="ok", verified_at=timezone.now())  # constraint hu_ok_requires_verified_at
        hus = list(HandlingUnit.objects.select_related("shipment__customer"))
        fam = families_in_progress(hus)
        self.assertIn("200", fam)
        k_std = rank_key(HandlingUnit.objects.get(pk=self.old.pk), fam)
        k_other = rank_key(oldest_other, fam)
        self.assertLess(k_std, k_other)

    def test_take_next_reserves_vip(self):
        r = self.client.post(reverse("ui:hu_take_next"))
        self.vip.refresh_from_db()
        self.assertEqual(self.vip.assigned_to_id, self.u.id)
        self.assertRedirects(r, reverse("ui:hu_control_detail", args=[self.vip.pk]),
                             fetch_redirect_response=False)

    def test_my_shift_renders(self):
        r = self.client.get(reverse("ui:hu_my_shift"))
        self.assertContains(r, "WEŹ NASTĘPNĄ")
        self.assertContains(r, self.vip.ref)  # następna = VIP

    def test_empty_queue_renders(self):
        HandlingUnit.objects.all().delete()
        r = self.client.get(reverse("ui:hu_my_shift"))
        self.assertContains(r, "Kolejka pusta")

    def test_queue_skips_history_but_keeps_families(self):
        # PERF-003: historia (ok/escaped) nie trafia do Pythona jako obiekty HU, a mimo
        # to klient z domkniętą paletą nadal liczy się jako „rozgrzebany".
        from django.db import connection
        from django.test import RequestFactory
        from django.test.utils import CaptureQueriesContext
        from django.utils import timezone
        from huctl.views.hu_dashboard import _queue
        HandlingUnit.objects.filter(pk=self.rec.pk).update(
            status="ok", verified_at=timezone.now())
        req = RequestFactory().get("/")
        req.user, req.session = self.u, {}
        # Wspólna kolejka (BIZ-005) biegnie przez _call_queue, a ten odpala sweep porzuconych
        # rezerwacji (throttle 1×/min, własny filtr status='planned'). Test pinuje zapytania
        # KOLEJKI — sweep wyciszamy throttlem, żeby wynik nie zależał od kolejności testów.
        from django.core.cache import cache
        cache.set("hu_res_sweep", 1, 60)
        with CaptureQueriesContext(connection) as ctx:
            all_hus, _takeable, fam = _queue(req)
        self.assertNotIn(self.rec.pk, {h.pk for h in all_hus})
        self.assertIn("200", fam)
        full_rows = [q["sql"] for q in ctx.captured_queries
                     if '"ui_handlingunit"."code"' in q["sql"]]
        self.assertTrue(full_rows)
        for sql in full_rows:
            self.assertIn("'to_recheck'", sql)       # filtr statusów w SQL, nie w Pythonie
