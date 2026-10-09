"""Regresja B-001 / B-002 / B-003 (BUGS-FOUND.md) — uprawnienia wg decyzji P-1 i P-3.

- B-001: snapshot 3D ``/magazyn/<pk>/`` (także ``?fmt=json``) — jak moduł „magazyn”
- B-002: media wewnętrzne (quality, hu_control, phv, ewm_tasks) tylko po zalogowaniu;
  od ACL-001 domyślnie WSZYSTKIE media wymagają logowania (publiczne tylko quotes/, site/)
- B-003: przeliczenie instrukcji (jedna / wszystkie) — tylko Admin / Master Data
"""
import os
import shutil
import tempfile

from django.test import TestCase, override_settings
from django.urls import reverse

from testkit import factories as f
from testkit.personas import client_for


class SnapshotAccessTests(TestCase):          # B-001
    @classmethod
    def setUpTestData(cls):
        cls.snap = f.SnapshotFactory()
        f.SnapshotRowFactory(snapshot=cls.snap)

    def test_anon_redirected_to_login_html_and_json(self):
        url = reverse("ui:warehouse_map_detail", args=[self.snap.pk])
        for suffix in ("", "?fmt=json"):
            with self.subTest(suffix=suffix):
                r = client_for("anon").get(url + suffix)
                self.assertEqual(r.status_code, 302)
                self.assertIn("/login/", r["Location"])

    def test_module_roles_in_others_out(self):
        url = reverse("ui:warehouse_map_detail", args=[self.snap.pk])
        expected = {"superuser": 200, "Administratorzy": 200, "Master Data": 200, "Transport": 200,
                    "Podgląd": 200, "Kontrola HU": 403, "Lider kontroli": 403, "Magazyn": 403,
                    "Obsługa klienta": 403, "Optymalizacja kartonów": 403, "bez_roli": 403}
        for persona, code in expected.items():
            with self.subTest(persona=persona):
                self.assertEqual(client_for(persona).get(url).status_code, code)


class InternalMediaTests(TestCase):           # B-002
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.enterContext(override_settings(MEDIA_ROOT=self.root))
        self.files = {}
        for folder in ("quality", "hu_control/2026/09", "phv/2026/09", "phv/loc/2026/09",
                       "ewm_tasks", "carton_artwork"):
            os.makedirs(os.path.join(self.root, folder), exist_ok=True)
            name = f"{folder}/plik.png"
            with open(os.path.join(self.root, name), "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")
            self.files[folder] = "/media/" + name

    def test_internal_folders_require_login(self):
        anon, user = client_for("anon"), client_for("bez_roli")
        for folder in ("quality", "hu_control/2026/09", "phv/2026/09", "phv/loc/2026/09", "ewm_tasks"):
            with self.subTest(folder=folder):
                r = anon.get(self.files[folder])
                self.assertEqual(r.status_code, 302)
                self.assertIn("/login/", r["Location"])
                self.assertEqual(user.get(self.files[folder]).status_code, 200)

    def test_artwork_requires_login(self):      # ACL-001 — grafiki opakowań nie są publiczne
        r = client_for("anon").get(self.files["carton_artwork"])
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])


class RecalculateAccessTests(TestCase):       # B-003
    @classmethod
    def setUpTestData(cls):
        cls.instr = f.InstructionFactory()

    def test_only_admin_and_master_data(self):
        urls = (reverse("ui:recalculate_all"),
                reverse("ui:planner_instruction_recalculate_async", args=[self.instr.pk]))
        expected = {"Podgląd": 403, "Obsługa klienta": 403, "Transport": 403, "bez_roli": 403,
                    "Master Data": 200, "Administratorzy": 200, "superuser": 200}
        for url in urls:
            for persona, code in expected.items():
                with self.subTest(url=url, persona=persona):
                    self.assertEqual(client_for(persona).post(url).status_code, code)
