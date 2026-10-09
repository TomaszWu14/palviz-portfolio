"""Audyt UX-004 (WCAG 2.2.1): szczegóły importu zadań EWM nie przeładowują się co 5 s
(meta-refresh). W trakcie importu strona odpytuje lekki endpoint statusu
(fetch) i ogłasza postęp w regionie aria-live; pełne przeładowanie tylko raz, po końcu.
Strażnik repo: meta-refresh wolno mieć wyłącznie ekranowi TV (ui/control/tv.html)."""
import re
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from ui.roles import GROUP_MASTER_DATA
from wh3d.models import WarehouseTaskBatch

WEB_DIR = Path(__file__).resolve().parents[2]
META_REFRESH = re.compile(r"""http-equiv\s*=\s*["']?refresh""", re.IGNORECASE)
ALLOWED_META_REFRESH = {"ui/control/tv.html"}      # ekran TV lidera — świadomy wyjątek (kiosk)


class EwmTasksPollingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.md = User.objects.create_user("md", password="x")
        cls.md.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        cls.plain = User.objects.create_user("plain", password="x")
        cls.running = WarehouseTaskBatch.objects.create(name="W toku", status="running")
        cls.done = WarehouseTaskBatch.objects.create(name="Gotowy", status="done", row_count=42,
                                                     message="Zaimportowano 42 zadania.")

    def _status(self, batch):
        return self.client.get(reverse("ui:ewm_tasks_status", args=[batch.pk]))

    def test_pending_detail_polls_without_meta_refresh(self):
        self.client.force_login(self.md)
        r = self.client.get(reverse("ui:ewm_tasks_detail", args=[self.running.pk]))
        self.assertContains(r, "Import trwa w tle")
        self.assertNotRegex(r.content.decode(), META_REFRESH)
        self.assertContains(r, 'aria-live="polite"')
        self.assertContains(r, f'data-status-url="{reverse("ui:ewm_tasks_status", args=[self.running.pk])}"')
        self.assertContains(r, "Wstrzymaj odświeżanie")

    def test_finished_detail_has_no_polling_hook(self):
        self.client.force_login(self.md)
        r = self.client.get(reverse("ui:ewm_tasks_detail", args=[self.done.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "data-status-url")
        self.assertNotRegex(r.content.decode(), META_REFRESH)

    def test_status_pending(self):
        self.client.force_login(self.md)
        r = self._status(self.running)
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-store", r["Cache-Control"])
        data = r.json()
        self.assertEqual((data["status"], data["pending"], data["stale"]), ("running", True, False))
        self.assertEqual(data["status_display"], "Import trwa")

    def test_status_done(self):
        self.client.force_login(self.md)
        data = self._status(self.done).json()
        self.assertEqual((data["status"], data["pending"], data["stale"]), ("done", False, False))
        self.assertEqual((data["row_count"], data["message"]), (42, "Zaimportowano 42 zadania."))

    def test_status_stale_is_not_pending(self):
        """Import „w tle” ponad STALE_AFTER = proces padł — polling ma się zatrzymać."""
        self.client.force_login(self.md)
        WarehouseTaskBatch.objects.filter(pk=self.running.pk).update(uploaded_at=timezone.now() - timedelta(hours=7))
        data = self._status(self.running).json()
        self.assertEqual((data["pending"], data["stale"]), (False, True))

    def test_status_requires_master_data(self):
        r = self._status(self.running)
        self.assertEqual(r.status_code, 302)                        # anonim → logowanie
        self.client.force_login(self.plain)
        self.assertEqual(self._status(self.running).status_code, 403)
        self.client.force_login(self.md)
        self.assertEqual(self.client.get(reverse("ui:ewm_tasks_status", args=[999999])).status_code, 404)


class MetaRefreshGuardTests(SimpleTestCase):
    def test_only_tv_screen_uses_meta_refresh(self):
        found = set()
        for tpl in WEB_DIR.glob("*/templates/**/*.html"):
            if META_REFRESH.search(tpl.read_text(encoding="utf-8")):
                found.add("/".join(tpl.relative_to(WEB_DIR).parts[2:]))   # <app>/templates/<nazwa>
        self.assertIn("ui/control/tv.html", found)                           # skan faktycznie widzi szablony
        self.assertEqual(found, ALLOWED_META_REFRESH,
                         "Meta-refresh przeładowuje całą stronę bez kontroli użytkownika (WCAG 2.2.1) — "
                         "użyj pollingu fetch() + aria-live (wzór: ui/ewm_tasks/detail.html).")
