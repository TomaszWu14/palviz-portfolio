"""Leader KPI: positions/hour over NET working time (walking counted, breaks excluded)
and error rate — the basis for the controller productivity bonus."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Shipment, HandlingUnit, HandlingUnitItem, HUControlAttempt
from ui.roles import GROUP_ADMIN, GROUP_LEADER, GROUP_CONTROLLER


def _user(*groups, name="u"):
    u = get_user_model().objects.create_user(username=name, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class KpiNetTimeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.leader = _user(GROUP_ADMIN, GROUP_LEADER, name="lead")
        cls.ctrl = _user(GROUP_CONTROLLER, name="c1")
        cls.sh = Shipment.objects.create(name="S")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HU1")
        # 3 RÓŻNE pozycje (distinct): first not timed, then two 30 s gaps → 60 s net working time.
        # Distinct = 3, więc przepustowość (po distinct, F3) = 180 pozycji/h.
        for i, (res, gap) in enumerate([("ok", None), ("ok", 30), ("error", 30)]):
            item = HandlingUnitItem.objects.create(hu=cls.hu, ref_code=f"A{i}", base_qty=1)
            HUControlAttempt.objects.create(hu=cls.hu, item=item, controller=cls.ctrl,
                                            result=res, seconds_since_prev=gap)

    def test_positions_per_hour_and_error_rate(self):
        self.client.force_login(self.leader)
        r = self.client.get(reverse("ui:hu_control_kpi"))
        self.assertEqual(r.status_code, 200)
        row = next(x for x in r.context["rows"] if x["controller"] == "c1")
        self.assertEqual(row["positions"], 3)            # próby (audyt)
        self.assertEqual(row["distinct_positions"], 3)   # unikalne pozycje (podstawa premii, F3)
        self.assertEqual(row["net_sec"], 60)             # two 30 s gaps
        self.assertEqual(row["pos_per_h"], 180.0)        # 3 distinct / (60/3600 h)
        self.assertEqual(row["errors"], 1)
        self.assertEqual(row["error_rate"], 33.3)

    def test_csv_export_has_new_columns(self):
        self.client.force_login(self.leader)
        r = self.client.get(reverse("ui:hu_control_kpi") + "?export=csv")
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        body = r.content.decode("utf-8")
        self.assertIn("Pozycje/h", body)
        self.assertIn("% wykrytych", body)               # F3: relabel „% błędów” → wykryte (jakość)


class RelogTimingNotSkippedTests(TestCase):
    """Regresja furtki KPI: wyloguj/zaloguj przed pozycją zerowało timing (sesyjny
    „first count after login"). Gap liczy się teraz zawsze z DB (poprzednia próba);
    przerwy wycina odczyt (KPI_MAX_GAP_SECONDS), nie zapis."""

    def setUp(self):
        self.ctrl = _user(GROUP_CONTROLLER, name="c9")
        self.sh = Shipment.objects.create(name="S9")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="H9",
                                              status="in_control", controlled_by=self.ctrl,
                                              warehouse_type="92EX")
        self.i1 = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A1", base_qty=1)
        self.i2 = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A2", base_qty=1)

    def _count(self, item):
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, item.pk]),
                         {"qty_base": "1", "action": "confirm"})

    def test_relogin_between_counts_still_timed(self):
        self.client.force_login(self.ctrl)
        self._count(self.i1)
        self.client.logout()
        self.client.force_login(self.ctrl)     # „przerwa" przez re-login
        self._count(self.i2)
        att = HUControlAttempt.objects.filter(item=self.i2).latest("created_at")
        self.assertIsNotNone(att.seconds_since_prev)   # timing nie do wyzerowania sesją

    def test_very_first_count_has_no_gap(self):
        self.client.force_login(self.ctrl)
        self._count(self.i1)
        att = HUControlAttempt.objects.filter(item=self.i1).latest("created_at")
        self.assertIsNone(att.seconds_since_prev)      # brak poprzedniej próby = brak gapu
