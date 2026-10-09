"""Słownik pojęć Data Center: seed rodzajów procesu + widok katalogu."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from ui.models import DictionaryEntry


class ProcessTypeSeedTests(TestCase):
    def test_seed_present_and_unique(self):
        qs = DictionaryEntry.objects.filter(category="process_type")
        self.assertGreater(qs.count(), 70)                    # cała lista SAP wjechała
        # brak duplikatów kodu (constraint + migracja idempotentna)
        self.assertEqual(qs.count(), qs.values("code").distinct().count())

    def test_known_codes_and_groups(self):
        e = DictionaryEntry.objects.get(category="process_type", code="PICK")
        self.assertEqual(e.label, "Wydanie z magazynu")
        self.assertEqual(e.group, "Wydanie z magazynu")
        self.assertEqual(
            DictionaryEntry.objects.get(category="process_type", code="1010").group,
            "Umieszczenie w magazynie")


class DictionaryViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("dic_admin", "a@a.pl", "x")
        self.client.force_login(self.admin)

    def test_renders_process_types(self):
        r = self.client.get(reverse("ui:data_center_dictionary"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Słownik pojęć")
        self.assertContains(r, "Wydanie z magazynu")

    def test_search_filters(self):
        r = self.client.get(reverse("ui:data_center_dictionary"), {"q": "GEIS"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "GEIS")
        self.assertNotContains(r, "Inwentaryzacja")          # odfiltrowane
