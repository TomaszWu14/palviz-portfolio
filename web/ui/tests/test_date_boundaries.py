"""„Dziś" = dzień lokalny (Europe/Warsaw), nie zegar maszyny — audyt TEST-008.

1. Strażnik: testy nie liczą dnia z zegara systemowego (`date.today()`, `datetime.now()`,
   `….now().date()` — ta ostatnia daje dzień UTC). Dzień bierzemy z `timezone.localdate()`
   albo zamrażamy zegar (`testkit.clock.frozen`); ten wzorzec wywracał `test_quote`
   po północy UTC (#729).
2. Logika „dziś" (przypomnienie o zaległym zadaniu, zadania cykliczne) działa na granicach:
   23:30 UTC (w Warszawie już następny dzień), przełom roku, obie zmiany czasu. Gdyby
   produkcja liczyła dzień w UTC, 23:30 UTC dałoby wczorajszą datę i testy by padły."""
import re
from datetime import datetime, timedelta, timezone as dt_tz
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from testkit.clock import KEY_DATES, frozen
from ui.models import RecurringTask, Task
from ui.roles import GROUP_MASTER_DATA
from ui.tasks import _generate_due_recurring

WEB = Path(__file__).resolve().parents[2]
# 23:30 UTC 28.09 = 01:30 w Warszawie 29.09 — data UTC i lokalna są różne.
UTC_LATE = datetime(2026, 9, 28, 23, 30, tzinfo=dt_tz.utc)
BOUNDARIES = {
    "utc_2330": UTC_LATE,
    "koniec_roku": KEY_DATES["koniec_roku"],
    "nowy_rok": KEY_DATES["nowy_rok"],
    "dst_wiosna_po": KEY_DATES["dst_wiosna_po"],
    "dst_jesien": KEY_DATES["dst_jesien"],
}
_SYSTEM_CLOCK = re.compile(r"\bdate\.today\(\)|\bdatetime\.now\(\)|\bnow\(\)\.date\(\)")


class SystemClockGuardTests(SimpleTestCase):
    def test_boundary_is_really_a_different_day(self):
        with frozen(UTC_LATE):
            self.assertNotEqual(timezone.localdate(), timezone.now().date())

    def test_tests_do_not_use_system_clock_for_today(self):
        me = Path(__file__).resolve()
        offenders = []
        for path in sorted(WEB.glob("*/tests/**/*.py")):
            if path.resolve() == me:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _SYSTEM_CLOCK.search(line):
                    offenders.append(f"{path.relative_to(WEB)}:{n}: {line.strip()}")
        self.assertEqual(offenders, [], "Dzień z timezone.localdate() albo testkit.clock.frozen() "
                                        "(TEST-008):\n" + "\n".join(offenders))


class LocalDayBoundaryTests(TestCase):
    def test_overdue_reminder_uses_local_day(self):
        user = get_user_model().objects.create_user("md-granica", password="x")
        user.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        for name, when in BOUNDARIES.items():
            with self.subTest(name), frozen(when):
                today = timezone.localdate()
                task = Task.objects.create(title=f"Zaległe {name}", assignee=user,
                                           due_date=today - timedelta(days=1))
                self.client.force_login(user)       # sesja w „zamrożonym" czasie
                self.client.get(reverse("ui:tasks_home"))
                task.refresh_from_db()
                self.assertEqual(task.overdue_last_reminded, today)

    def test_recurring_generation_uses_local_day(self):
        for name, when in BOUNDARIES.items():
            with self.subTest(name), frozen(when):
                RecurringTask.objects.all().delete()
                today = timezone.localdate()
                tpl = RecurringTask.objects.create(title=f"Cykliczne {name}", interval="weekly",
                                                   next_run=today)
                self.assertEqual(_generate_due_recurring(), 1)      # domyślne „dziś"
                task = Task.objects.get(title=f"Cykliczne {name}", category="recurring")
                self.assertEqual(task.due_date, today)
                tpl.refresh_from_db()
                self.assertEqual(tpl.next_run, today + timedelta(days=7))
