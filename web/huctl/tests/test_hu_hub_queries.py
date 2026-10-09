"""Hub kontroli nie może ściągać całej tabeli HU do Pythona.

Mapa typów magazynu budowana była pętlą po `hus.values("warehouse_type", "status")` —
czyli po KAŻDYM wierszu. Po imporcie stocku (setki tysięcy palet) to ładowanie całego
zbioru na każde wejście na stronę, żeby policzyć kilkanaście liczników."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from ui.models import HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN


class HubAggregationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="hub", password="x")
        cls.user.groups.add(Group.objects.get_or_create(name=GROUP_ADMIN)[0])
        cls.sh = Shipment.objects.create(name="D1")
        # Trzy typy magazynu × dwa statusy, wiele palet w każdej kombinacji.
        seq = 0
        cls.expected = {}
        for wt in ("WT01", "WT02", ""):
            for status, count in (("planned", 4), ("to_recheck", 2)):
                for _ in range(count):
                    seq += 1
                    HandlingUnit.objects.create(shipment=cls.sh, seq=seq, code=f"H{seq}",
                                                warehouse_type=wt, status=status)
                cls.expected[(wt, status)] = count

    def setUp(self):
        self.client.force_login(self.user)

    def _rows(self):
        resp = self.client.get(reverse("ui:hu_control_hub"))
        self.assertEqual(resp.status_code, 200)
        return {r["code"]: r for r in resp.context["wh_type_rows"]}

    def test_counts_are_correct_per_type_and_status(self):
        rows = self._rows()
        for (wt, status), count in self.expected.items():
            self.assertEqual(rows[wt][status], count, f"{wt}/{status}")
        for wt in ("WT01", "WT02", ""):
            self.assertEqual(rows[wt]["total"], 6)

    def test_type_map_is_aggregated_in_sql(self):
        # Liczba ZAPYTAŃ nie jest tu miarą — wersja iterująca też robiła jedno zapytanie,
        # tylko ściągała nim każdy wiersz. Mierzalna różnica jest w kształcie SQL:
        # agregat ma GROUP BY i COUNT, wersja naiwna gołe SELECT warehouse_type, status.
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse("ui:hu_control_hub"))

        candidates = [q["sql"] for q in ctx.captured_queries
                      if "warehouse_type" in q["sql"] and "status" in q["sql"]
                      and "ui_handlingunit" in q["sql"]]
        self.assertTrue(candidates, "nie znaleziono zapytania budującego mapę typów")
        aggregated = [s for s in candidates
                      if "GROUP BY" in s.upper() and "COUNT(" in s.upper()]
        self.assertTrue(
            aggregated,
            "mapa typów liczona w Pythonie zamiast GROUP BY:\n" + "\n".join(candidates))

    def test_grouping_survives_the_models_default_ordering(self):
        # HandlingUnit.Meta.ordering = [shipment, seq]; gdyby wyciekło do GROUP BY,
        # agregacja rozpadłaby się na pojedyncze wiersze i liczniki pokazałyby 1.
        rows = self._rows()
        self.assertEqual(rows["WT01"]["planned"], 4)
