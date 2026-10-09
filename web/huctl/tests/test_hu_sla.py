"""SLA / termin wysyłki HU (#18): poziomy, etykieta i wpływ na ranking kolejki."""
import datetime

from django.test import TestCase, override_settings
from django.utils import timezone

from huctl.sla import deadline_for, sla_for
from huctl.views.hu_dashboard import rank_key
from ui.models import HandlingUnit, Shipment


@override_settings(HU_SLA_CUTOFF_HOUR=12, HU_SLA_SOON_MIN=60)
class SlaTests(TestCase):
    def _hu(self, delivery_date, seq=1):
        sh = Shipment.objects.create(name=f"D{seq}", kunnr="200",
                                     outbound_delivery_date=delivery_date)
        return HandlingUnit.objects.create(shipment=sh, seq=seq, code=f"H{seq}",
                                           warehouse_type="92EX")

    def test_no_date_is_level_none(self):
        hu = HandlingUnit.objects.create(
            shipment=Shipment.objects.create(name="DX", kunnr="200"),
            seq=9, code="H9", warehouse_type="92EX")
        s = sla_for(hu)
        self.assertEqual(s.level, "none")
        self.assertEqual(s.label, "")
        self.assertIsNone(s.minutes)

    def test_deadline_uses_cutoff_hour(self):
        hu = self._hu(datetime.date(2026, 9, 10))
        dl = deadline_for(hu)
        self.assertEqual(dl.hour, 12)
        self.assertEqual((dl.year, dl.month, dl.day), (2026, 9, 10))

    def test_levels_overdue_soon_ok(self):
        hu = self._hu(datetime.date(2026, 9, 10))
        dl = deadline_for(hu)
        overdue = dl + datetime.timedelta(minutes=30)   # „teraz" po terminie
        soon = dl - datetime.timedelta(minutes=30)       # 30 min do terminu (< 60)
        ok = dl - datetime.timedelta(hours=5)            # 5 h do terminu
        self.assertEqual(sla_for(hu, now=overdue).level, "overdue")
        self.assertEqual(sla_for(hu, now=soon).level, "soon")
        self.assertEqual(sla_for(hu, now=ok).level, "ok")
        self.assertTrue(sla_for(hu, now=overdue).label.startswith("po terminie"))
        self.assertTrue(sla_for(hu, now=soon).label.startswith("zostało"))

    def test_ranking_nearer_deadline_first_in_same_category(self):
        far = self._hu(datetime.date(2026, 9, 20), seq=1)
        near = self._hu(datetime.date(2026, 9, 10), seq=2)
        none = HandlingUnit.objects.create(
            shipment=Shipment.objects.create(name="DZ", kunnr="200"),
            seq=3, code="H3", warehouse_type="92EX")
        now = timezone.make_aware(datetime.datetime(2026, 9, 9, 8, 0))
        hus = [far, near, none]
        order = sorted(hus, key=lambda h: rank_key(h, set(), now=now))
        # ten sam REST → bliższy termin (near) przed dalszym (far); brak terminu na końcu.
        self.assertEqual([h.pk for h in order], [near.pk, far.pk, none.pk])
