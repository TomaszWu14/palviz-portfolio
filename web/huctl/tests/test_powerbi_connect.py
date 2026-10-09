"""In-browser Power BI connect: the 'Połącz' button starts a delegated device-code login
(returns the code), a background thread finishes it, and the panel polls until connected."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.roles import GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_CONTROLLER

_FLOW = {"user_code": "ABC-123", "verification_uri": "https://microsoft.com/devicelogin",
         "expires_in": 900, "message": "Wejdź na ... i wpisz ABC-123"}


_seq = 0


def _user(*groups):
    global _seq
    _seq += 1
    u = get_user_model().objects.create_user(username="u%d" % _seq, password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class PowerBIConnectTests(TestCase):
    def setUp(self):
        self.md = _user(GROUP_MASTER_DATA)

    @patch("ui.powerbi.complete_device_flow", lambda flow: "")     # no-op background work
    @patch("ui.powerbi.start_device_flow", lambda: _FLOW)
    @patch("ui.powerbi.has_dataset_config", lambda: True)
    def test_connect_returns_device_code(self):
        self.client.force_login(self.md)
        resp = self.client.post(reverse("ui:planner_stock_powerbi_connect"))
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["user_code"], "ABC-123")
        self.assertIn("devicelogin", d["verification_uri"])

    @patch("ui.powerbi.has_dataset_config", lambda: False)
    def test_connect_blocked_without_dataset(self):
        self.client.force_login(self.md)
        resp = self.client.post(reverse("ui:planner_stock_powerbi_connect"))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    @patch("ui.powerbi.start_device_flow", side_effect=RuntimeError("AADSTS-SECRET tenant=acme"))
    @patch("ui.powerbi.has_dataset_config", lambda: True)
    def test_connect_start_error_hides_raw_exception(self, _m):
        # INT-006: bez surowego tekstu wyjątku w odpowiedzi JSON.
        self.client.force_login(self.md)
        with self.assertLogs("ui.powerbi", "ERROR"):
            resp = self.client.post(reverse("ui:planner_stock_powerbi_connect"))
        self.assertEqual(resp.status_code, 502)
        self.assertNotIn("AADSTS-SECRET", resp.content.decode())

    def test_connect_requires_post(self):
        self.client.force_login(self.md)
        self.assertEqual(self.client.get(reverse("ui:planner_stock_powerbi_connect")).status_code, 405)

    def test_connect_forbidden_for_non_master_data(self):
        self.client.force_login(_user(GROUP_CONTROLLER))   # controller is not Master Data/Admin
        resp = self.client.post(reverse("ui:planner_stock_powerbi_connect"))
        self.assertEqual(resp.status_code, 403)

    @patch("ui.powerbi.connected_account", lambda: "jan@example.com")
    @patch("ui.powerbi.is_configured", lambda: True)
    def test_status_reports_connected(self):
        self.client.force_login(self.md)
        d = self.client.get(reverse("ui:planner_stock_powerbi_status")).json()
        self.assertTrue(d["connected"])
        self.assertEqual(d["account"], "jan@example.com")

    @patch("ui.powerbi.start_device_flow", lambda: _FLOW)
    @patch("ui.powerbi.has_dataset_config", lambda: True)
    def test_connect_dispatches_celery_task_in_production(self):
        """Bez eager (produkcja): dokończenie flow idzie przez zadanie Celery,
        nie wątek-demon (przeżywa shutdown, błąd trafia do record_error)."""
        from unittest.mock import MagicMock
        self.client.force_login(self.md)
        with self.settings(CELERY_TASK_ALWAYS_EAGER=False), \
                patch("ui.tasks.complete_powerbi_device_flow") as task:
            task.delay = MagicMock()
            resp = self.client.post(reverse("ui:planner_stock_powerbi_connect"))
        self.assertTrue(resp.json()["ok"])
        task.delay.assert_called_once_with(_FLOW)

    def test_task_records_error_on_failure(self):
        from ui.tasks import complete_powerbi_device_flow
        with patch("ui.powerbi.complete_device_flow", side_effect=RuntimeError("expired")), \
                patch("ui.powerbi.record_error") as rec:
            out = complete_powerbi_device_flow.run(_FLOW)
        self.assertFalse(out["ok"])
        rec.assert_called_once()

    @patch("ui.powerbi.is_configured", lambda: False)
    @patch("ui.powerbi.has_dataset_config", lambda: True)
    def test_panel_shows_connect_button_when_dataset_set(self):
        self.client.force_login(_user(GROUP_ADMIN))
        resp = self.client.get(reverse("ui:planner_stock"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Połącz z Power BI")
        self.assertContains(resp, reverse("ui:planner_stock_powerbi_connect"))
