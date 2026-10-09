import io

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from ui.management.commands.seed_test_users import TEST_USERS
from ui.roles import GROUP_CONTROLLER


def _run(*args):
    call_command("seed_test_users", *args, stdout=io.StringIO(), stderr=io.StringIO())


@override_settings(DEBUG=True)
class SeedTestUsersTest(TestCase):
    def test_creates_one_account_per_role_password_equals_login(self):
        _run()
        self.assertEqual(User.objects.filter(username__startswith="test_").count(), len(TEST_USERS))
        for username, group_name in TEST_USERS:
            user = User.objects.get(username=username)
            self.assertTrue(user.check_password(username), f"hasło != login dla {username}")
            self.assertFalse(user.profile.must_change_password)
            if group_name is None:
                self.assertTrue(user.is_superuser)
            else:
                self.assertTrue(user.groups.filter(name=group_name).exists())

    def test_idempotent_rerun_does_not_duplicate(self):
        _run()
        _run()
        self.assertEqual(User.objects.filter(username__startswith="test_").count(), len(TEST_USERS))

    def test_controller_account_is_control_only(self):
        _run()
        from ui.roles import is_control_only
        user = User.objects.get(username="test_kontrola")
        self.assertTrue(user.groups.filter(name=GROUP_CONTROLLER).exists())
        self.assertTrue(is_control_only(user))


class SeedTestUsersProdGuardTest(TestCase):
    @override_settings(DEBUG=False)
    def test_refuses_on_prod_without_force(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            _run()
        self.assertEqual(User.objects.filter(username__startswith="test_").count(), 0)

    @override_settings(DEBUG=False)
    def test_force_no_longer_bypasses_prod_guard(self):
        # SEC-012: --force usunięte — żadna flaga nie obchodzi odmowy przy DEBUG=false.
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            _run("--force")
        with self.assertRaises(CommandError):
            _run("--drop")
        self.assertEqual(User.objects.filter(username__startswith="test_").count(), 0)
