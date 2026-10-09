"""Reguły pakowania klient×indeks: edycja Master Data na karcie klienta, plakietka
przy pozycji w kontroli HU tylko dla właściwej pary klient×indeks."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import (Customer, CustomerPackagingRule, HandlingUnit,
                       HandlingUnitItem, Product, Shipment)
from ui.roles import GROUP_CONTROLLER, GROUP_MASTER_DATA


def _user(name, group):
    u = get_user_model().objects.create_user(username=name, password="x")
    u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


class RuleEditTests(TestCase):
    def setUp(self):
        self.md = _user("md", GROUP_MASTER_DATA)
        self.customer = Customer.objects.create(name="Klient X", code="KX")
        self.product = Product.objects.create(code="CTND-200_V1", name="Kompresy")
        self.client.force_login(self.md)

    def test_add_and_delete_rule(self):
        self.client.post(reverse("ui:customer_rule_add", args=[self.customer.pk]),
                         {"ref_code": "CTND-200_V1", "units_per_pack": "25",
                          "note": "woreczki z etykietą"})
        rule = CustomerPackagingRule.objects.get(customer=self.customer, product=self.product)
        self.assertEqual(rule.units_per_pack, 25)
        # Duplikat pary → podmiana wartości, nie błąd.
        self.client.post(reverse("ui:customer_rule_add", args=[self.customer.pk]),
                         {"ref_code": "CTND-200_V1", "units_per_pack": "50"})
        rule.refresh_from_db()
        self.assertEqual(rule.units_per_pack, 50)
        self.client.post(reverse("ui:customer_rule_delete", args=[rule.pk]))
        self.assertEqual(CustomerPackagingRule.objects.count(), 0)

    def test_unknown_ref_and_bad_qty_rejected(self):
        for data in ({"ref_code": "NIE-MA", "units_per_pack": "25"},
                     {"ref_code": "CTND-200_V1", "units_per_pack": "0"}):
            self.client.post(reverse("ui:customer_rule_add", args=[self.customer.pk]), data)
        self.assertEqual(CustomerPackagingRule.objects.count(), 0)


class HuBadgeTests(TestCase):
    def setUp(self):
        self.ctrl = _user("ctrl", GROUP_CONTROLLER)
        self.customer = Customer.objects.create(name="Klient X", code="KX")
        self.product = Product.objects.create(code="CTND-200_V1", name="Kompresy")
        ship = Shipment.objects.create(name="S1", customer=self.customer)
        self.hu = HandlingUnit.objects.create(shipment=ship, seq=1, status="in_control",
                                              controlled_by=self.ctrl)
        HandlingUnitItem.objects.create(hu=self.hu, ref_code="CTND-200_V1",
                                        product=self.product, base_qty=250)
        CustomerPackagingRule.objects.create(customer=self.customer, product=self.product,
                                             units_per_pack=25)
        self.client.force_login(self.ctrl)

    def test_badge_shown_for_matching_rule(self):
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertContains(r, "25 szt/opak.")
        self.assertContains(r, "Wymaganie klienta")

    def test_no_badge_for_other_customer(self):
        other = Customer.objects.create(name="Inny", code="IN")
        self.hu.shipment.customer = other
        self.hu.shipment.save(update_fields=["customer"])
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertNotContains(r, "Wymaganie klienta")


class RuleAlertTests(TestCase):
    """Alert mailowy: dostawa klienta z indeksem > szt/opak. → zadanie (dedup) + mail."""
    def setUp(self):
        self.customer = Customer.objects.create(name="SZPITAL PUM", code="PUM",
                                                kunnr="20001238")
        self.product = Product.objects.create(code="CTND-200_V1", name="Kompresy")
        CustomerPackagingRule.objects.create(
            customer=self.customer, product=self.product, units_per_pack=25,
            alert_email="planista@example.com")
        ship = Shipment.objects.create(name="Dostawa 1", customer=self.customer,
                                       wz_number="WZ-123")
        self.hu = HandlingUnit.objects.create(shipment=ship, seq=1)
        self.item = HandlingUnitItem.objects.create(
            hu=self.hu, ref_code="CTND-200_V1", product=self.product, base_qty=250)

    def test_alert_task_created_once_with_delivery_data(self):
        from django.core import mail
        from django.test import override_settings
        from ui.models import Task
        from ui.notifications import run_packaging_rule_alerts
        with override_settings(EMAIL_HOST="smtp.test",
                               EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            self.assertEqual(run_packaging_rule_alerts(), 1)
            task = Task.objects.get(dedup_key__startswith="packrule:")
            self.assertIn("CTND-200_V1", task.title)
            self.assertIn("WZ-123", task.description)          # numer dostawy w treści
            self.assertIn("25 szt/opak", task.description)
            self.assertEqual(len(mail.outbox), 1)
            self.assertEqual(mail.outbox[0].to, ["planista@example.com"])
            # Ponowny skan stocku → bez duplikatu.
            self.assertEqual(run_packaging_rule_alerts(), 0)

    def test_teams_webhook_called_when_configured(self):
        from unittest.mock import patch
        from django.test import override_settings
        from ui.notifications import run_packaging_rule_alerts
        with override_settings(TEAMS_WEBHOOK_URL="https://example.test/hook"), \
             patch("requests.post") as post:
            post.return_value.raise_for_status = lambda: None
            self.assertEqual(run_packaging_rule_alerts(), 1)
            self.assertTrue(post.called)
            payload = post.call_args.kwargs["json"]
            card_text = str(payload)
            self.assertIn("CTND-200_V1", card_text)
            self.assertIn("WZ-123", card_text)

    def test_qty_at_threshold_no_alert(self):
        from ui.notifications import run_packaging_rule_alerts
        self.item.base_qty = 25          # równe progowi = zgodne z regułą
        self.item.save(update_fields=["base_qty"])
        self.assertEqual(run_packaging_rule_alerts(), 0)

    def test_op_base_unit_converted_to_pieces(self):
        # JP=OP: 10 OP × 12 szt/OP = 120 szt > 25 → alert; 2 OP = 24 szt → brak.
        from ui.models import PalletizationInstruction
        from ui.notifications import run_packaging_rule_alerts
        PalletizationInstruction.objects.create(
            product=self.product, version=1, is_active=True, unit_weight=0.1,
            pcs_per_carton=10, carton_l=40, carton_w=30, carton_h=25,
            pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15,
            max_height_total_cm=200, units_per_piece=12)
        self.item.base_unit = "OP"
        self.item.base_qty = 2                     # 24 szt — poniżej progu
        self.item.save(update_fields=["base_unit", "base_qty"])
        self.assertEqual(run_packaging_rule_alerts(), 0)
        self.item.base_qty = 10                    # 120 szt — alert
        self.item.save(update_fields=["base_qty"])
        self.assertEqual(run_packaging_rule_alerts(), 1)
        from ui.models import Task
        self.assertIn("120 szt", Task.objects.get(dedup_key__startswith="packrule:").title)

    def test_teams_only_rule_alerts_without_email(self):
        # Reguła bez alert_email dalej alarmuje (zadanie+Teams), gdy webhook ustawiony.
        from unittest.mock import patch
        from django.core import mail
        from django.test import override_settings
        from ui.models import CustomerPackagingRule
        from ui.notifications import run_packaging_rule_alerts
        CustomerPackagingRule.objects.update(alert_email="")
        self.assertEqual(run_packaging_rule_alerts(), 0)   # bez maila i webhooka — cisza
        with override_settings(TEAMS_WEBHOOK_URL="https://example.test/hook"), \
             patch("requests.post") as post:
            post.return_value.raise_for_status = lambda: None
            self.assertEqual(run_packaging_rule_alerts(), 1)
            self.assertTrue(post.called)
            self.assertEqual(len(mail.outbox), 0)          # maila brak — celowo
