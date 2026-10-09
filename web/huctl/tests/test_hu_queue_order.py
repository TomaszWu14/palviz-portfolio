"""Kolejka 'Następna HU': remis kategorii i terminu rozstrzyga FIFO, nie objętość.

BIZ-005 / Q-42: jeden ranking („Weź następną") dla obu wejść — dawne tiebreakery SQL
„Następnej HU" (objętość, liczba pozycji) odpadły razem z osobnym rankingiem."""
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from ui.models import Shipment, HandlingUnit
from huctl.views.hu_dashboard import _queue


class QueueOrderTests(TestCase):
    def _order(self, user):
        req = RequestFactory().get("/")
        req.user, req.session = user, {}
        return [h.code for h in _queue(req)[1]]

    def test_fifo_not_volume_on_tie(self):
        admin = User.objects.create_superuser("adm", "a@a.pl", "x")
        sh = Shipment.objects.create()                       # brak klienta/daty → remis rankingu
        HandlingUnit.objects.create(shipment=sh, seq=1, code="BIG", status="planned",
                                    length_cm=100, width_cm=100, height_cm=100)
        HandlingUnit.objects.create(shipment=sh, seq=2, code="SMALL", status="planned",
                                    length_cm=10, width_cm=10, height_cm=10)
        self.assertEqual(self._order(admin), ["BIG", "SMALL"])   # starsza najpierw
