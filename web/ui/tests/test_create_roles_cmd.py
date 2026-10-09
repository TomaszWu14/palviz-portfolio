"""Komenda create_roles — tworzy dokładnie 9 grup z roles.py, idempotentnie (TEST-004)."""

from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from ui.roles import ALL_GROUPS, GROUP_ADMIN


def _run(*args):
    out = StringIO()
    call_command("create_roles", *args, stdout=out)
    return out.getvalue()


class CreateRolesCommandTests(TestCase):
    def setUp(self):
        Group.objects.all().delete()  # migracje mogą zasiać grupy — startujemy od zera

    def test_creates_exactly_the_nine_groups(self):
        self.assertEqual(len(ALL_GROUPS), 9)
        out = _run()
        self.assertEqual(set(Group.objects.values_list("name", flat=True)), set(ALL_GROUPS))
        self.assertEqual(Group.objects.count(), 9)
        self.assertEqual(out.count("Utworzono grupę"), 9)
        self.assertIn("Gotowe.", out)

    def test_idempotent_second_run(self):
        _run()
        ids = set(Group.objects.values_list("pk", flat=True))
        out = _run()
        self.assertEqual(set(Group.objects.values_list("pk", flat=True)), ids)
        self.assertEqual(out.count("Grupa już istnieje"), 9)
        self.assertNotIn("Utworzono grupę", out)

    def test_superuser_created_and_added_to_admin_group(self):
        with mock.patch("getpass.getpass", return_value="tajne-haslo") as gp:
            out = _run("--superuser", "szef", "--email", "szef@example.com")
        gp.assert_called_once()
        user = get_user_model().objects.get(username="szef")
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.email, "szef@example.com")
        self.assertTrue(user.check_password("tajne-haslo"))
        self.assertTrue(user.groups.filter(name=GROUP_ADMIN).exists())
        self.assertIn("Superuser 'szef' utworzony", out)

    def test_existing_superuser_not_touched(self):
        get_user_model().objects.create_user("szef", password="stare")
        with mock.patch("getpass.getpass") as gp:
            out = _run("--superuser", "szef")
        gp.assert_not_called()
        self.assertIn("już istnieje", out)
        self.assertFalse(get_user_model().objects.get(username="szef").is_superuser)
