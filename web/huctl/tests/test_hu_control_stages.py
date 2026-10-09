"""In-app HU control transaction: scan → count positions → finalize."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Shipment, HandlingUnit

from .test_hu_control import _user_all_roles

class HUControlStage1Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.sh = Shipment.objects.create(name="D-S1")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HUX", status="planned")
        cls.it = cls.hu.items.create(ref_code="R1", base_unit="OP", base_qty=10,
                                     alt_unit="KAR", alt_qty=5)

    def setUp(self):
        self.client.force_login(self.user)

    def test_ok_hu_is_locked_from_counting(self):
        from django.utils import timezone as _tz
        self.hu.status = "ok"; self.hu.verified_at = _tz.now(); self.hu.save()
        resp = self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.it.pk]),
                                {"action": "confirm", "qty_base": "10"})
        self.assertRedirects(resp, reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.it.refresh_from_db()
        self.assertFalse(self.it.controlled)               # not counted — HU is locked

    def test_takeover_reassigns_controller(self):
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create_user(username="other", password="x")
        self.hu.status = "in_control"; self.hu.controlled_by = other; self.hu.save()
        self.client.post(reverse("ui:hu_control_takeover", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.controlled_by, self.user)

    def test_recheck_full_shows_all_positions(self):
        self.it.controlled = True; self.it.result = "ok"; self.it.save()
        bad = self.hu.items.create(ref_code="R2", base_qty=4, controlled=True, result="error")
        self.hu.status = "to_recheck"; self.hu.save()
        only_err = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertEqual(len(only_err.context["items"]), 1)          # only the error
        full = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]), {"full": "1"})
        self.assertEqual(len(full.context["items"]), 2)              # full recount


class HUControlStage2Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.sh = Shipment.objects.create(name="D-S2")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HUQ", status="in_control")
        cls.it = cls.hu.items.create(ref_code="R1", base_unit="OP", base_qty=10, alt_unit="KAR", alt_qty=5)

    def setUp(self):
        self.client.force_login(self.user)

    def test_quality_flag_creates_issue_and_blocks_until_closed(self):
        from ui.models import HUQualityIssue
        # Correct quantity + a quality flag → item OK (quantity), but the open quality issue
        # now BLOCKS posting until it is closed; closing it then allows finalize → ok.
        # (wrong_batch needs no photo — damage/bad-placement do; see test_quality_rules.)
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.it.pk]),
                         {"action": "confirm", "qty_base": "10", "flag_wrong_batch": "on"})
        self.it.refresh_from_db()
        self.assertEqual(self.it.result, "ok")                       # quality ≠ quantity error
        issue = HUQualityIssue.objects.get(hu=self.hu, issue_type="wrong_batch", status="open")
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertNotEqual(self.hu.status, "ok")                    # blocked by open issue
        # Close the issue, then posting succeeds.
        self.client.post(reverse("ui:hu_quality_close", args=[issue.pk]), {"resolution_note": "ok"})
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "ok")

    def test_quantity_mismatch_still_recontrols(self):
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.it.pk]),
                         {"action": "confirm", "sure": "1", "qty_base": "9"})       # short by 1
        self.it.refresh_from_db()
        self.assertEqual(self.it.result, "error")
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "to_recheck")

    def test_close_quality_issue(self):
        from ui.models import HUQualityIssue
        iss = HUQualityIssue.objects.create(hu=self.hu, item=self.it, issue_type="damaged",
                                            raised_by=self.user)
        self.client.post(reverse("ui:hu_quality_close", args=[iss.pk]),
                         {"resolution_note": "wymieniono karton"})
        iss.refresh_from_db()
        self.assertEqual(iss.status, "closed")
        self.assertEqual(iss.closed_by, self.user)
        self.assertEqual(iss.resolution_note, "wymieniono karton")

    def test_add_foreign_item(self):
        from ui.models import HUQualityIssue
        self.client.post(reverse("ui:hu_quality_add_foreign", args=[self.hu.pk]),
                         {"ref_code": "OBCY-1", "qty": "3", "hu_code": self.hu.ref})
        iss = HUQualityIssue.objects.get(hu=self.hu, issue_type="foreign_item")
        self.assertEqual(iss.ref_code, "OBCY-1")
        self.assertEqual(iss.qty, 3)


class HUControlStage3Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.sh = Shipment.objects.create(name="D-S3")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HUA",
                                             status="in_control", picker="JKOWALSKI")
        cls.i1 = cls.hu.items.create(ref_code="R1", base_unit="OP", base_qty=10, alt_unit="KAR", alt_qty=5)
        cls.i2 = cls.hu.items.create(ref_code="R2", base_unit="OP", base_qty=4, alt_unit="KAR", alt_qty=2)

    def setUp(self):
        self.client.force_login(self.user)

    def test_attempt_recorded_with_snapshot_and_timing(self):
        from ui.models import HUControlAttempt
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.i1.pk]),
                         {"action": "confirm", "qty_base": "10"})
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.i2.pk]),
                         {"action": "confirm", "qty_base": "4"})
        atts = HUControlAttempt.objects.filter(controller=self.user).order_by("created_at")
        self.assertEqual(atts.count(), 2)
        self.assertEqual(atts[0].result, "ok")
        self.assertEqual(atts[0].exp_base_qty, 10)          # expected snapshot kept
        self.assertIsNone(atts[0].seconds_since_prev)        # first position has no gap
        self.assertIsNotNone(atts[1].seconds_since_prev)     # second has time-per-position

    def test_error_report_uses_hu_picker(self):
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.i1.pk]),
                         {"action": "confirm", "sure": "1", "qty_base": "9"})   # quantity error
        resp = self.client.get(reverse("ui:hu_error_report"), {"picker": "JKOWAL"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "R1")
        self.assertContains(resp, "JKOWALSKI")


class HUControlStage4Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from ui.roles import GROUP_LEADER, GROUP_CONTROLLER
        cls.leader = get_user_model().objects.create_user(username="lead", password="x")
        cls.leader.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        cls.ctrl = get_user_model().objects.create_user(username="op", password="x")
        cls.ctrl.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        cls.sh = Shipment.objects.create(name="D-S4")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HUK", status="in_control")
        cls.i1 = cls.hu.items.create(ref_code="R1", base_unit="OP", base_qty=10, alt_unit="KAR", alt_qty=5)
        cls.i2 = cls.hu.items.create(ref_code="R2", base_unit="OP", base_qty=4, alt_unit="KAR", alt_qty=2)

    def test_kpi_is_leader_only(self):
        self.client.force_login(self.ctrl)
        self.assertEqual(self.client.get(reverse("ui:hu_control_kpi")).status_code, 403)
        self.client.force_login(self.leader)
        self.assertEqual(self.client.get(reverse("ui:hu_control_kpi")).status_code, 200)

    def test_kpi_counts_positions_and_errors(self):
        self.client.force_login(self.ctrl)
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.i1.pk]),
                         {"action": "confirm", "qty_base": "10"})           # ok
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.i2.pk]),
                         {"action": "confirm", "sure": "1", "qty_base": "3"})  # error
        self.client.force_login(self.leader)
        rows = self.client.get(reverse("ui:hu_control_kpi"), {"period": "month"}).context["rows"]
        row = next(r for r in rows if r["controller"] == "op")
        self.assertEqual(row["positions"], 2)
        self.assertEqual(row["hus"], 1)
        self.assertEqual(row["errors"], 1)

    def test_gap_always_recorded_from_db(self):
        # Gap liczy się zawsze z DB (poprzednia próba kontrolera) — re-login go nie zeruje
        # (stary sesyjny „first count after login" był furtką do zerowania KPI). Pierwszy
        # count w OGÓLE (brak poprzedniej próby) ma None; przerwy wycina odczyt
        # (KPI_MAX_GAP_SECONDS w _kpi_stats), nie zapis.
        from ui.models import HUControlAttempt

        def last():
            return (HUControlAttempt.objects.filter(controller=self.ctrl)
                    .order_by("-created_at").first())

        self.client.force_login(self.ctrl)
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.i1.pk]),
                         {"action": "confirm", "qty_base": "10"})
        self.assertIsNone(last().seconds_since_prev)            # brak poprzedniej próby
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.i2.pk]),
                         {"action": "confirm", "qty_base": "4"})
        self.assertIsNotNone(last().seconds_since_prev)         # subsequent — timed
        self.client.logout(); self.client.force_login(self.ctrl)   # re-login ≠ reset zegara
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.i1.pk]),
                         {"action": "confirm", "qty_base": "10"})
        self.assertIsNotNone(last().seconds_since_prev)         # nadal mierzone

    def test_reopen_is_leader_only(self):
        from django.utils import timezone as _tz
        self.hu.status = "ok"; self.hu.verified_at = _tz.now(); self.hu.save()
        self.client.force_login(self.ctrl)
        self.assertEqual(self.client.post(reverse("ui:hu_control_reopen", args=[self.hu.pk])).status_code, 403)
        self.client.force_login(self.leader)
        self.client.post(reverse("ui:hu_control_reopen", args=[self.hu.pk]), {"reason": "pomyłka kontroli"})
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "in_control")


class HUQualityPhotoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.sh = Shipment.objects.create(name="D-PH")
        cls.hu = HandlingUnit.objects.create(shipment=cls.sh, seq=1, code="HUP", status="in_control")

    def setUp(self):
        self.client.force_login(self.user)

    def _png(self):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        buf = io.BytesIO(); Image.new("RGB", (2, 2), (200, 0, 0)).save(buf, "PNG")
        return SimpleUploadedFile("dmg.png", buf.getvalue(), content_type="image/png")

    def test_foreign_item_with_photo(self):
        import tempfile
        from django.test import override_settings
        from ui.models import HUQualityIssue
        with tempfile.TemporaryDirectory() as md, override_settings(MEDIA_ROOT=md):
            self.client.post(reverse("ui:hu_quality_add_foreign", args=[self.hu.pk]),
                             {"ref_code": "X1", "qty": "1", "hu_code": self.hu.ref, "photo": self._png()})
            iss = HUQualityIssue.objects.get(hu=self.hu, issue_type="foreign_item")
            self.assertTrue(iss.photo)
            self.assertTrue(iss.photo.name.endswith(".png"))

    def test_attach_photo_to_existing_issue(self):
        import tempfile
        from django.test import override_settings
        from ui.models import HUQualityIssue
        iss = HUQualityIssue.objects.create(hu=self.hu, issue_type="damaged", raised_by=self.user)
        with tempfile.TemporaryDirectory() as md, override_settings(MEDIA_ROOT=md):
            self.client.post(reverse("ui:hu_quality_attach", args=[iss.pk]), {"photo": self._png()})
            iss.refresh_from_db()
            self.assertTrue(iss.photo)
