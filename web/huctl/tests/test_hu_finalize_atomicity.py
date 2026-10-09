"""Liczenie obowiązkowe (B1): niepoliczone pozycje BLOKUJĄ księgowanie i nie są nigdy
mutowane (koniec auto-OK). Policzone → HU księguje się normalnie."""
import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import HandlingUnit, HandlingUnitItem, Shipment
from ui.roles import ALL_GROUPS


def _user(name="fin"):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class FinalizeMandatoryCountTests(TestCase):
    def setUp(self):
        self.user = _user()
        self.client.force_login(self.user)
        self.sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(
            shipment=self.sh, seq=1, code="FIN1", status="in_control")

    def _item(self, **kw):
        kw.setdefault("ref_code", "RG-50")
        kw.setdefault("base_unit", "OP")
        kw.setdefault("base_qty", 10)
        return HandlingUnitItem.objects.create(hu=self.hu, **kw)

    def test_untouched_items_block_and_stay_untouched(self):
        it = self._item(lot="L1")
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]), {})  # bez liczenia
        it.refresh_from_db()
        self.assertFalse(it.controlled, "niepoliczona pozycja nie może zostać zmutowana")
        self.assertEqual(it.result, "")
        self.assertIsNone(it.controlled_at)
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "in_control")       # zablokowane, nie zaksięgowane

    def test_counted_clean_hu_posts_ok(self):
        it = self._item(lot="L1", expiry=timezone.localdate() + datetime.timedelta(days=900))
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty),
                          "expiry_ok": "1"})       # data ważności potwierdzona (obowiązkowe)
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        it.refresh_from_db()
        self.assertTrue(it.controlled)
        self.assertEqual(it.result, "ok")
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "ok")
        self.assertIsNotNone(self.hu.verified_at)
