"""Auto-otwarcie pierwszej niesprawdzonej pozycji na karcie HU (grill pyt. 2/8)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import HandlingUnit, HandlingUnitItem, Shipment
from ui.roles import ALL_GROUPS


class FirstPosOpenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="c", password="x")
        for g in ALL_GROUPS:
            cls.user.groups.add(Group.objects.get_or_create(name=g)[0])
        sh = Shipment.objects.create(name="D1")
        cls.hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="H1",
                                             status="in_control",
                                             controlled_by=cls.user)
        cls.i1 = HandlingUnitItem.objects.create(hu=cls.hu, ref_code="A1",
                                                 base_qty=5, base_unit="OP")
        cls.i2 = HandlingUnitItem.objects.create(hu=cls.hu, ref_code="A2",
                                                 base_qty=3, base_unit="OP")

    def setUp(self):
        self.client.force_login(self.user)

    def test_first_unchecked_position_is_open(self):
        html = self.client.get(
            reverse("ui:hu_control_detail", args=[self.hu.pk])).content.decode()
        self.assertIn(f'id="pos-{self.i1.pk}" open', html)
        self.assertNotIn(f'id="pos-{self.i2.pk}" open', html)

    def test_after_counting_next_position_opens(self):
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.i1.pk]),
                         {"action": "confirm", "qty_base": "5"})
        html = self.client.get(
            reverse("ui:hu_control_detail", args=[self.hu.pk])).content.decode()
        self.assertNotIn(f'id="pos-{self.i1.pk}" open', html)
        self.assertIn(f'id="pos-{self.i2.pk}" open', html)
