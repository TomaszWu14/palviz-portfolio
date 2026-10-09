"""Regression: a Redis/cache outage must not 500 the app. nbp.get_rate touches the
cache before any try/except; when Redis was down its ConnectionError propagated and took
down every page that looked up an FX rate (notably the shipments list). The cache is
optional — a miss/outage should degrade to a live fetch (or None), never raise."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui import nbp
from ui.models import Shipment, ShipmentQuoteOffer
from ui.roles import ALL_GROUPS


class _DeadCache:
    """Stand-in for django-redis when Redis is refused — every op raises."""
    def get(self, *a, **k):
        raise RuntimeError("Redis ConnectionError: Connection refused")

    def set(self, *a, **k):
        raise RuntimeError("Redis ConnectionError: Connection refused")


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"rates": [{"mid": 4.32}]}


class NbpCacheOutageTests(TestCase):
    def test_get_rate_returns_none_when_cache_and_api_down(self):
        with patch("ui.nbp.cache", _DeadCache()), \
             patch("requests.get", side_effect=Exception("no network")):
            self.assertIsNone(nbp.get_rate("EUR"))

    def test_get_rate_fetches_live_when_cache_down(self):
        # Cache read AND write both fail, but NBP is reachable → still return the rate.
        with patch("ui.nbp.cache", _DeadCache()), patch("requests.get", return_value=_Resp()):
            self.assertEqual(nbp.get_rate("EUR"), Decimal("4.32"))

    def test_shipments_list_renders_when_cache_down(self):
        u = get_user_model().objects.create_user(username="p", password="x")
        for g in ALL_GROUPS:
            u.groups.add(Group.objects.get_or_create(name=g)[0])
        sh = Shipment.objects.create(name="X")
        ShipmentQuoteOffer.objects.create(shipment=sh, carrier_name="DHL",
                                          amount=Decimal("100"), currency="EUR",
                                          submitted_at=timezone.now())
        self.client.force_login(u)
        with patch("ui.nbp.cache", _DeadCache()), patch("requests.get", side_effect=Exception("down")):
            resp = self.client.get(reverse("ui:planner_shipments"))
        self.assertEqual(resp.status_code, 200)
