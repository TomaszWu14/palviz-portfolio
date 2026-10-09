"""CODE-002: błędy powiadomień nie są już połykane bez śladu.

- reprezentatywna ścieżka: lider przydziela HU, notify() rzuca → przydział i tak
  zapisany (semantyka „powiadomienie nie wywraca akcji”), a wyjątek trafia do logu;
- strażnik AST: w huctl/ i transport/ żadnego `except Exception: pass` (poza allowlistą).
"""
import ast
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase, TestCase

from ui.models import HandlingUnit, Shipment
from ui.roles import GROUP_CONTROLLER, GROUP_LEADER

WEB = Path(__file__).resolve().parents[2]
# Ścieżki (względem web/), gdzie `except Exception: pass` jest świadome i opisane komentarzem.
ALLOWLIST: set[str] = set()


class NotifyFailureIsLoggedTests(TestCase):
    def test_assign_succeeds_and_logs_when_notify_raises(self):
        leader = User.objects.create_user("lider", password="x")
        leader.groups.add(Group.objects.get_or_create(name=GROUP_LEADER)[0])
        ctrl = User.objects.create_user("kontroler", password="x")
        ctrl.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        hu = HandlingUnit.objects.create(shipment=Shipment.objects.create(name="D-N1"),
                                         seq=1, code="HUN1", status="planned")
        self.client.force_login(leader)
        with mock.patch("ui.notifications.notify", side_effect=RuntimeError("smtp/db down")), \
                self.assertLogs("huctl.views.hu_leader", level="ERROR") as cm:
            r = self.client.post(f"/control/hu/{hu.pk}/assign/", {"assignee": ctrl.pk})
        self.assertEqual(r.status_code, 302)
        hu.refresh_from_db()
        self.assertEqual(hu.assigned_to, ctrl)
        self.assertIn("smtp/db down", "\n".join(cm.output))


def _silent_broad_excepts(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.ExceptHandler)
                and isinstance(node.type, ast.Name) and node.type.id == "Exception"
                and len(node.body) == 1 and isinstance(node.body[0], ast.Pass)):
            yield node.lineno


class NoSilentBroadExceptGuardTests(SimpleTestCase):
    def test_no_except_exception_pass_in_huctl_and_transport(self):
        found = []
        for app in ("huctl", "transport"):
            for p in (WEB / app).rglob("*.py"):
                rel = p.relative_to(WEB).as_posix()
                if "/tests/" in rel or "/migrations/" in rel or rel in ALLOWLIST:
                    continue
                found += [f"{rel}:{ln}" for ln in _silent_broad_excepts(p)]
        self.assertEqual(found, [], "`except Exception: pass` bez logu — użyj log.exception(...)")
