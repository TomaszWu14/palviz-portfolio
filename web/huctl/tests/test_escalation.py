"""Spec wywołań — eskalacja niekompletnej przesyłki: Taski wg EscalationRoute (dedup),
status zwrotny picking_eta_note wraca na badge grupy i baner kontrolera."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import (Shipment, HandlingUnit, EscalationRoute, Task)
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER


def _user(name, group=GROUP_CONTROLLER):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


class EscalationTests(TestCase):
    def setUp(self):
        self.ctrl = _user("ctrl")
        self.pick_lead = _user("pick_lead", GROUP_LEADER)
        self.area_lead = _user("area_lead", GROUP_LEADER)
        EscalationRoute.objects.create(warehouse_type="",           # reguła globalna
                                       picking_leader=self.pick_lead,
                                       area_leader=self.area_lead,
                                       escalate_after_minutes=30)   # kierownik NIE od razu
        self.sh = Shipment.objects.create(name="D-esc", picking_complete=False)
        HandlingUnit.objects.create(shipment=self.sh, seq=1, code="E1",
                                    warehouse_type="0052")
        self.client.force_login(self.ctrl)

    def _escalate(self):
        return self.client.post(reverse("ui:hu_escalate"), {"shipment_id": self.sh.pk})

    def test_creates_tasks_for_route_with_dedup(self):
        self._escalate()
        keys = set(Task.objects.values_list("dedup_key", flat=True))
        self.assertEqual(len(keys), 2)                          # picking + area leader
        self.assertTrue(all(k.startswith(f"picking_incomplete:{self.sh.pk}:") for k in keys))
        self._escalate()                                        # ponowna eskalacja
        self.assertEqual(Task.objects.count(), 2)               # dedup — bez duplikatów

    def test_exact_warehouse_route_wins_over_global(self):
        shift = _user("shift", GROUP_LEADER)
        EscalationRoute.objects.create(warehouse_type="0052", picking_leader=shift,
                                       escalate_after_minutes=0)
        self._escalate()
        self.assertEqual(Task.objects.get().assignee, shift)    # reguła 0052, nie globalna

    def test_complete_shipment_not_escalated(self):
        self.sh.picking_complete = True
        self.sh.save(update_fields=["picking_complete"])
        self._escalate()
        self.assertEqual(Task.objects.count(), 0)

    def test_eta_note_roundtrip_to_badge_and_banner(self):
        # Lider odpowiada ETA…
        self.client.force_login(self.pick_lead)
        self.client.post(reverse("ui:shipment_eta_note", args=[self.sh.pk]),
                         {"note": "paleta 7 w toku / ETA 14:30"})
        self.sh.refresh_from_db()
        self.assertEqual(self.sh.picking_eta_note, "paleta 7 w toku / ETA 14:30")
        self.assertEqual(self.sh.picking_eta_by, self.pick_lead)
        # …baner kontrolera na detalu HU pokazuje status zwrotny.
        self.client.force_login(self.ctrl)
        hu = self.sh.handling_units.first()
        r = self.client.get(reverse("ui:hu_control_detail", args=[hu.pk]))
        self.assertContains(r, "paleta 7 w toku / ETA 14:30")

    def test_recipient_group_shows_incomplete_badge(self):
        self.sh.recipient_name = "Szpital X"
        self.sh.save(update_fields=["recipient_name"])
        adm = get_user_model().objects.create_user("adm", password="x", is_superuser=True)
        self.client.force_login(adm)
        r = self.client.get(reverse("ui:planner_stock_contents"),
                            {"view": "hu", "group": "recipient"})
        self.assertContains(r, "NIEKOMPLETNA")
        self.assertContains(r, "Eskaluj")
