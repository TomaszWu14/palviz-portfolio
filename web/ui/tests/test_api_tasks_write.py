"""Szew IN: POST /api/v2/tasks — zapis zwrotny z automatu (n8n). Auth X-API-Key,
idempotencja po dedup_key."""
import json

from django.test import TestCase, override_settings

from ui.models import Task

KEY = "test-key-123"
HDR = {"HTTP_X_API_KEY": KEY}


def _post(client, payload):
    return client.post("/api/v2/tasks", data=json.dumps(payload),
                       content_type="application/json", **HDR)


@override_settings(PALVIZ_API_TOKEN=KEY)
class TasksWriteApiTest(TestCase):
    def test_requires_api_key(self):
        r = self.client.post("/api/v2/tasks", data=json.dumps({"title": "X"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(Task.objects.count(), 0)

    def test_creates_task(self):
        r = _post(self.client, {"title": "Uzupełnij master data", "priority": "high",
                                "related_product_code": "DMO-1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["created"])
        t = Task.objects.get(pk=r.json()["id"])
        self.assertEqual(t.title, "Uzupełnij master data")
        self.assertEqual(t.priority, "high")
        self.assertEqual(t.related_product_code, "DMO-1")
        self.assertEqual(t.category, "manual")

    def test_dedup_key_does_not_duplicate(self):
        r1 = _post(self.client, {"title": "A", "dedup_key": "n8n:md:1"})
        r2 = _post(self.client, {"title": "A ponownie", "dedup_key": "n8n:md:1"})
        self.assertTrue(r1.json()["created"])
        self.assertFalse(r2.json()["created"])
        self.assertEqual(r1.json()["id"], r2.json()["id"])
        self.assertEqual(Task.objects.filter(dedup_key="n8n:md:1").count(), 1)

    def test_done_task_does_not_block_new(self):
        r1 = _post(self.client, {"title": "A", "dedup_key": "n8n:md:2"})
        Task.objects.filter(pk=r1.json()["id"]).update(status="done")
        r2 = _post(self.client, {"title": "A znów", "dedup_key": "n8n:md:2"})
        self.assertTrue(r2.json()["created"])                 # zamknięte nie blokuje
        self.assertEqual(Task.objects.filter(dedup_key="n8n:md:2").count(), 2)

    def test_empty_title_rejected(self):
        r = _post(self.client, {"title": "   "})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Task.objects.count(), 0)

    def test_db_constraint_blocks_duplicate_open_task(self):
        """Twarda gwarancja: nawet z pominięciem pre-checku w widoku (przegrany wyścig
        między workerami) DB odrzuca drugi otwarty Task z tym samym dedup_key."""
        from django.db import IntegrityError, transaction
        Task.objects.create(title="A", dedup_key="race:1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Task.objects.create(title="B", dedup_key="race:1")
        # done nie blokuje; pusty dedup_key nie podlega constraintowi.
        Task.objects.create(title="C", dedup_key="race:2", status="done")
        Task.objects.create(title="D", dedup_key="race:2")
        Task.objects.create(title="E", dedup_key="")
        Task.objects.create(title="F", dedup_key="")

    def test_lost_race_returns_winner_task(self):
        """API: IntegrityError na create (wyścig) → 200 z zadaniem zwycięzcy."""
        from unittest.mock import patch
        winner = Task.objects.create(title="Zwycięzca", dedup_key="race:3")
        # Pre-check "nie widzi" zwycięzcy (symulacja okna wyścigu) → create → IntegrityError.
        real_filter = Task.objects.filter
        calls = {"n": 0}

        def _filter(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:                       # tylko pierwszy pre-check ślepy
                return Task.objects.none()
            return real_filter(*a, **kw)
        with patch("ui.api.Task.objects.filter", side_effect=_filter):
            r = _post(self.client, {"title": "Przegrany", "dedup_key": "race:3"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["created"])
        self.assertEqual(r.json()["id"], winner.id)
