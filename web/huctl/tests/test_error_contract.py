"""Kontrakt pętli rozbieżności (grill 2026-09-05, pyt. 33): ŻADNA gałąź błędu nie może
kończyć się ciszą. Każda aktywna flaga błędu → otwarte HUQualityIssue; rozbieżność
ilościowa → Task naprawczy przy finalizacji. Test piniuje macierz — nowa flaga bez
obsługi wywali CI."""
import io

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import (HandlingUnit, HandlingUnitItem, HUQualityIssue, Shipment,
                       Task)
from ui.roles import GROUP_CONTROLLER


def _png():
    # Minimalny poprawny 1×1 PNG (dowód-foto dla flag damaged/bad_placement).
    import base64
    data = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
        b"AAAABQABh6FO1AAAAABJRU5ErkJggg==")
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile("p.png", data, content_type="image/png")


class ErrorContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="ctrl", password="x")
        cls.user.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])

    def setUp(self):
        self.client.force_login(self.user)
        self.sh = Shipment.objects.create(name="Dostawa")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1,
                                              status="in_control",
                                              controlled_by=self.user)
        self.item = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A1",
                                                    base_qty=5, base_unit="OP")

    def _count(self, **extra):
        data = {"action": "confirm", "qty_base": "5", **extra}
        return self.client.post(
            reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]), data)

    def test_every_quality_flag_opens_issue(self):
        quality_flags = [k for k, _ in HandlingUnitItem.ACTIVE_ERROR_FLAGS
                         if k not in HandlingUnitItem.QUANTITY_FLAGS]
        self.assertTrue(quality_flags)          # macierz nie może być pusta
        for key in quality_flags:
            with self.subTest(flag=key):
                sh = Shipment.objects.create(name=f"D-{key}")
                hu = HandlingUnit.objects.create(shipment=sh, seq=1,
                                                 status="in_control",
                                                 controlled_by=self.user)
                it = HandlingUnitItem.objects.create(hu=hu, ref_code="A1",
                                                     base_qty=5, base_unit="OP")
                data = {"action": "confirm", "qty_base": "5", f"flag_{key}": "on"}
                files = {}
                # damaged/bad_placement wymagają dowodu-foto — dołącz PNG.
                self.client.post(
                    reverse("ui:hu_control_count", args=[hu.pk, it.pk]),
                    {**data, "photo": _png()})
                self.assertTrue(
                    HUQualityIssue.objects.filter(hu=hu, item=it, issue_type=key,
                                                  status="open").exists(),
                    f"flaga {key} nie utworzyła HUQualityIssue — gałąź kończy się ciszą")

    def test_quantity_shortage_raises_task_on_finalize(self):
        # Rozbieżność ilościowa (blind recount → sure=1) → error → Task przy finalizacji.
        self._count(qty_base="3", sure="1")
        self.item.refresh_from_db()
        self.assertEqual(self.item.result, "error")
        with self.captureOnCommitCallbacks(execute=True):   # taski idą po commicie
            self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.assertTrue(Task.objects.filter(category="hu_fix",
                                            related_hu=self.hu).exists(),
                        "niedobór ilościowy nie utworzył Taska naprawczego")

    def test_quantity_excess_raises_task_on_finalize(self):
        self._count(qty_base="9", sure="1")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        t = Task.objects.filter(category="hu_fix", related_hu=self.hu).first()
        self.assertIsNotNone(t, "nadmiar ilościowy nie utworzył Taska naprawczego")
        self.assertIn("nadmiar", t.title.lower())
