"""PERF-006: strażnicy skalowania liczby zapytań na najczęściej otwieranych listach rdzenia.

Każdy test mierzy ekran przy 1 i przy 8 wierszach i wymaga tej samej liczby zapytań
(porównanie względne — patrz testkit/queries.py). Listy już pilnowane gdzie indziej
(Data Center, macierz opakowań, optymalizacja kartonów) nie są tu dublowane."""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from testkit import factories as f
from testkit.queries import QueryScalingMixin
from ui.roles import GROUP_MASTER_DATA


class _MDScreens(QueryScalingMixin, TestCase):
    def setUp(self):
        self.user = f.UserFactory(username="perf_md", groups=[GROUP_MASTER_DATA])
        self.client.force_login(self.user)
        self.seq = 0


class TasksListQueryScalingTests(_MDScreens):
    """Lista zadań (`ui:tasks_home`): zadania z wykonawcą i autorem + powiadomienia użytkownika."""

    def _grow(self, n):
        today = timezone.localdate()
        for _ in range(n):
            self.seq += 1
            assignee = f.UserFactory(username=f"perf_wyk{self.seq}", groups=[GROUP_MASTER_DATA])
            f.TaskFactory(assignee=assignee, created_by=self.user,
                          priority=("high", "normal", "low")[self.seq % 3],
                          due_date=today + timedelta(days=3))
            # Zadanie po terminie, już przypomniane dziś — wiersz „overdue” bez bocznego
            # efektu _alert_overdue (e-mail/SMS raz dziennie to nie koszt renderu listy).
            f.TaskFactory(assignee=assignee, created_by=self.user,
                          due_date=today - timedelta(days=2), overdue_last_reminded=today)
            f.NotificationFactory(recipient=self.user)

    def test_tasks_list_does_not_grow_with_rows(self):
        _, _, resp = self.assertQueriesFlat(reverse("ui:tasks_home"), self._grow)
        self.assertEqual(len(resp.context["tasks"]), 16)
        self.assertEqual(len(resp.context["notifs"]), 8)

    def test_mine_filter(self):
        # Filtr „moje” to osobna ścieżka queryseta — ten sam brak N+1.
        def grow(n):
            for _ in range(n):
                f.TaskFactory(assignee=self.user, created_by=self.user)
        _, _, resp = self.assertQueriesFlat(reverse("ui:tasks_home"), grow,
                                            params={"mine": "1"})
        self.assertEqual(len(resp.context["tasks"]), 8)


class MasterDataListsQueryScalingTests(_MDScreens):
    """Listy master data: produkty (z instrukcjami, kartonem i opakowaniem zbiorczym)
    i instrukcje paletyzacji — paginowane po 25, więc 8 wierszy mieści się na stronie."""

    def _grow_products(self, n):
        for _ in range(n):
            carton = f.CartonFactory(inner_pack=f.InnerPackFactory())
            f.InstructionFactory(product=f.ProductFactory(category=f.ProductCategoryFactory()),
                                 carton=carton)

    def test_products_list(self):
        _, _, resp = self.assertQueriesFlat(reverse("ui:planner_products"), self._grow_products)
        self.assertEqual(len(resp.context["page_obj"].object_list), 8)

    def test_instructions_list(self):
        _, _, resp = self.assertQueriesFlat(reverse("ui:planner_instructions"),
                                            self._grow_products)
        self.assertEqual(len(resp.context["page_obj"].object_list), 8)


class MessagesInboxQueryScalingTests(_MDScreens):
    """Skrzynka wiadomości (`ui:messages_inbox`) — wątki z wiadomościami i uczestnikami."""

    def _grow(self, n):
        for _ in range(n):
            self.seq += 1
            other = f.UserFactory(username=f"perf_rozm{self.seq}")
            thread = f.ThreadFactory(participants=[self.user, other], created_by=other)
            f.MessageFactory(thread=thread, sender=other)
            f.MessageFactory(thread=thread, sender=self.user, body="Sprawdzone, OK.")

    def test_inbox(self):
        _, _, resp = self.assertQueriesFlat(reverse("ui:messages_inbox"), self._grow)
        self.assertEqual(len(resp.context["threads"]), 8)
