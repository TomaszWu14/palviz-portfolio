"""Persistent HU queue + "vanished before control": an uncontrolled HU that stops
reappearing in the SAP feed is surfaced to the leader so nothing silently escapes."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import Shipment, HandlingUnit
from ui.roles import GROUP_ADMIN, GROUP_LEADER
from huctl.views.hu import _import_hu_rows


def _leader(name="lead"):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in (GROUP_ADMIN, GROUP_LEADER):
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class HuVanishTests(TestCase):
    def test_import_stamps_last_seen(self):
        ok, res = _import_hu_rows(["pickhu", "ref", "ilosc"], [["HU9", "A1", "5"]])
        self.assertTrue(ok)
        hu = HandlingUnit.objects.get(code="HU9")
        self.assertIsNotNone(hu.last_seen_at)          # persistent queue, stamped as seen

    def test_hub_flags_stale_uncontrolled(self):
        leader = _leader()
        sh = Shipment.objects.create(name="Stock", is_stock=True)
        now = timezone.now()
        stale = HandlingUnit.objects.create(shipment=sh, seq=1, code="STALE",
                                            status="planned", last_seen_at=now - timedelta(hours=48))
        fresh = HandlingUnit.objects.create(shipment=sh, seq=2, code="FRESH",
                                            status="planned", last_seen_at=now)
        legacy = HandlingUnit.objects.create(shipment=sh, seq=3, code="LEGACY",
                                             status="planned", last_seen_at=None)
        done = HandlingUnit.objects.create(shipment=sh, seq=4, code="DONE",
                                           status="ok", verified_at=now,
                                           last_seen_at=now - timedelta(hours=48))
        self.client.force_login(leader)
        r = self.client.get(reverse("ui:hu_control_hub"))
        stale_hus = r.context["stale_hus"]
        self.assertIn(stale, stale_hus)                # uncontrolled + not seen for 48h
        self.assertNotIn(fresh, stale_hus)             # seen just now
        self.assertNotIn(legacy, stale_hus)            # never stamped → unknown, not flagged
        self.assertNotIn(done, stale_hus)              # already controlled
