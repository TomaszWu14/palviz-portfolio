"""Recurring-task generation (Celery beat helper)."""
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from ui.models import RecurringTask, Task
from ui.tasks import _generate_due_recurring


class RecurringTaskTests(TestCase):
    def test_due_template_generates_task_and_advances(self):
        r = RecurringTask.objects.create(title="Inwentaryzacja", interval="weekly",
                                         next_run=timezone.localdate() - timedelta(days=1))
        created = _generate_due_recurring(today=timezone.localdate())
        self.assertEqual(created, 1)
        self.assertTrue(Task.objects.filter(title="Inwentaryzacja", category="recurring").exists())
        r.refresh_from_db()
        # Anti-dryf: next_run przesuwa się od SWOJEJ daty (today-1)+7, nie od today+7.
        # Faza harmonogramu zachowana — spóźnienie o dzień nie przesuwa terminu na stałe.
        self.assertEqual(r.next_run, timezone.localdate() + timedelta(days=6))

    def test_advances_past_today_without_drift_when_beat_missed_periods(self):
        # Zadanie tygodniowe spóźnione o 20 dni: next_run ma wylądować tuż ZA dzisiaj,
        # w fazie harmonogramu (wielokrotność 7 od pierwotnej daty), bez dryfu.
        anchor = timezone.localdate() - timedelta(days=20)
        r = RecurringTask.objects.create(title="Cotygodniowe", interval="weekly", next_run=anchor)
        _generate_due_recurring(today=timezone.localdate())
        r.refresh_from_db()
        self.assertGreater(r.next_run, timezone.localdate())
        self.assertEqual((r.next_run - anchor).days % 7, 0)

    def test_not_due_template_skipped(self):
        RecurringTask.objects.create(title="Później", interval="daily",
                                     next_run=timezone.localdate() + timedelta(days=2))
        self.assertEqual(_generate_due_recurring(today=timezone.localdate()), 0)

    def test_inactive_skipped(self):
        RecurringTask.objects.create(title="Wyłączone", interval="daily",
                                     next_run=timezone.localdate(), is_active=False)
        self.assertEqual(_generate_due_recurring(today=timezone.localdate()), 0)
