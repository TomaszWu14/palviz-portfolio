"""Pkt 5 — Warstwa A/C wymuszenia skanu HU.
- rozpoznanie skan vs klawiatura (scan_src / token DataWedge),
- log źródła inputu + device_id na HUControlAttempt,
- opcjonalne twarde wymuszenie skanu (HU_SCAN_ENFORCE)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from ui.models import HandlingUnit, HandlingUnitItem, HUControlAttempt, Shipment
from ui.roles import GROUP_CONTROLLER


def _ctrl():
    u = get_user_model().objects.create_user("c1", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
    return u


class FlagDeclarationTests(TestCase):
    def test_control_flags_declared_in_settings(self):
        # Strażnik review-fix: flagi muszą przechodzić przez AppEnv/settings (nie tylko
        # getattr z defaultem), inaczej nie da się ich włączyć z environmentu na prod.
        from django.conf import settings as s
        for name in ("HU_SCAN_TOKEN", "HU_SCAN_ENFORCE", "HU_PHOTO_ENFORCE", "HU_PHOTO_DETECT",
                     "HU_PHOTO_RETAIN_DAYS", "HU_RESERVED_MAX_HOURS"):
            self.assertTrue(hasattr(s, name), f"{name} niezadeklarowane w settings")


class ScanEnforceTests(TestCase):
    def setUp(self):
        self.u = _ctrl()
        self.client.force_login(self.u)
        self.sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="H1",
                                              status="in_control", controlled_by=self.u,
                                              warehouse_type="92EX")
        self.item = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A1",
                                                    base_unit="OP", base_qty=5)

    def test_scan_source_recorded_on_attempt(self):
        # skan (scan_src=scan) → sesja zapamiętuje źródło; licznik loguje input_source=scan.
        self.client.post(reverse("ui:hu_control_scan"),
                         {"code": "H1", "scan_src": "scan", "device_id": "DEV42"})
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
                         {"qty_base": "5", "action": "confirm"})
        att = HUControlAttempt.objects.filter(hu=self.hu).latest("created_at")
        self.assertEqual(att.input_source, "scan")
        self.assertEqual(att.device_id, "DEV42")

    @override_settings(HU_SCAN_TOKEN="]Z")
    def test_datawedge_token_stripped_and_marked_scan(self):
        r = self.client.post(reverse("ui:hu_control_scan"), {"code": "]ZH1"})
        self.assertRedirects(r, reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertEqual(self.client.session[f"hu_scan_src_{self.hu.pk}"], "scan")

    @override_settings(HU_SCAN_ENFORCE=True)
    def test_count_blocked_without_scan_when_enforced(self):
        # Luka: auto-next z kolejki / deep-link nie przechodzi przez hu_control_scan,
        # więc sesja nie ma hu_scan_src_<pk> — liczenie musi być zablokowane.
        r = self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
                             {"qty_base": "5", "action": "confirm"}, follow=True)
        self.assertContains(r, "SKANEREM")
        self.item.refresh_from_db()
        self.assertFalse(self.item.controlled)

    @override_settings(HU_SCAN_ENFORCE=True)
    def test_count_allowed_after_scan_when_enforced(self):
        self.client.post(reverse("ui:hu_control_scan"), {"code": "H1", "scan_src": "scan"})
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
                         {"qty_base": "5", "action": "confirm"})
        self.item.refresh_from_db()
        self.assertTrue(self.item.controlled)

    @override_settings(HU_SCAN_ENFORCE=True)
    def test_manual_entry_rejected_when_enforced(self):
        r = self.client.post(reverse("ui:hu_control_scan"),
                             {"code": "H1", "scan_src": "keyboard"}, follow=True)
        self.assertContains(r, "SKANEREM")
        # nie przeszło do karty HU
        self.assertNotContains(r, "Rozpocznij kontrolę")


class PhotoEnforceTests(TestCase):
    """Bramka fizycznej obecności: zdjęcie palety przed liczeniem, TYLKO na urządzeniach
    z aparatem (Zebra bez aparatu jedzie na samym skanie)."""
    def setUp(self):
        self.u = _ctrl()
        self.client.force_login(self.u)
        self.sh = Shipment.objects.create(name="D1")
        self.hu = HandlingUnit.objects.create(shipment=self.sh, seq=1, code="H1",
                                              status="in_control", controlled_by=self.u,
                                              warehouse_type="92EX")
        self.item = HandlingUnitItem.objects.create(hu=self.hu, ref_code="A1",
                                                    base_unit="OP", base_qty=5)

    def _set_device(self, dev):
        # last_device pochodzi z jawnego wyboru w sesji (session["device_type"]), a
        # PresenceMiddleware stempluje go na profil (throttle 60 s przez cache). Ustawiamy
        # tak jak realny flow i czyścimy cache, żeby stempel na pewno zaskoczył.
        from django.core.cache import cache
        cache.clear()
        s = self.client.session
        s["device_type"] = dev
        s.save()

    def _png(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile("hu.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, content_type="image/png")

    @override_settings(HU_PHOTO_ENFORCE=True)
    def test_count_blocked_without_photo_on_camera_device(self):
        self._set_device("mobile")
        r = self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
                             {"qty_base": "5", "action": "confirm"})
        self.assertRedirects(r, reverse("ui:hu_photo_check", args=[self.hu.pk]))
        self.item.refresh_from_db()
        self.assertFalse(self.item.controlled)

    @override_settings(HU_PHOTO_ENFORCE=True)
    def test_count_allowed_after_photo_on_camera_device(self):
        from ui.models import HUControlPhoto
        self._set_device("tablet")
        r = self.client.post(reverse("ui:hu_photo_check", args=[self.hu.pk]), {"photo": self._png()})
        self.assertRedirects(r, reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertEqual(HUControlPhoto.objects.filter(hu=self.hu).count(), 1)
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
                         {"qty_base": "5", "action": "confirm"})
        self.item.refresh_from_db()
        self.assertTrue(self.item.controlled)

    @override_settings(HU_PHOTO_ENFORCE=True)
    def test_zebra_counts_without_photo(self):
        self._set_device("zebra")   # bez aparatu → bramka foto przepuszcza
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
                         {"qty_base": "5", "action": "confirm"})
        self.item.refresh_from_db()
        self.assertTrue(self.item.controlled)

    @override_settings(HU_PHOTO_DETECT=True)
    def test_detect_mode_logs_skip_but_allows_count(self):
        from ui.models import HUStatusEvent
        self._set_device("mobile")   # aparat, ale tryb detekcji → nie blokuje
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
                         {"qty_base": "5", "action": "confirm"})
        self.item.refresh_from_db()
        self.assertTrue(self.item.controlled)   # policzone mimo braku zdjęcia
        self.assertTrue(HUStatusEvent.objects.filter(hu=self.hu, kind="photo").exists())

    @override_settings(HU_PHOTO_DETECT=True)
    def test_detect_mode_silent_for_zebra(self):
        from ui.models import HUStatusEvent
        self._set_device("zebra")    # bez aparatu → nic nie logujemy
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, self.item.pk]),
                         {"qty_base": "5", "action": "confirm"})
        self.assertFalse(HUStatusEvent.objects.filter(hu=self.hu, kind="photo").exists())

    @override_settings(HU_PHOTO_RETAIN_DAYS=30)
    def test_retention_purges_only_old_photos(self):
        from datetime import timedelta
        from django.utils import timezone
        from ui.models import HUControlPhoto
        from ui.tasks import purge_old_hu_control_photos
        old = HUControlPhoto.objects.create(hu=self.hu, user=self.u, photo=self._png())
        # auto_now_add — starą datę wpychamy update()em (create ignoruje przekazane created_at).
        HUControlPhoto.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))
        new = HUControlPhoto.objects.create(hu=self.hu, user=self.u, photo=self._png())
        res = purge_old_hu_control_photos()
        self.assertEqual(res["deleted"], 1)
        self.assertFalse(HUControlPhoto.objects.filter(pk=old.pk).exists())
        self.assertTrue(HUControlPhoto.objects.filter(pk=new.pk).exists())
