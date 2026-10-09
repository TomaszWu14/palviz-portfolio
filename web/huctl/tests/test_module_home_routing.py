"""Routing ekranu domowego per rola (wydzielone z test_hu_control.py — limit 500 linii)."""
"""In-app HU control transaction: scan → count positions → finalize."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.roles import ALL_GROUPS


def _user_all_roles(username="ctrl"):
    u = get_user_model().objects.create_user(username=username, password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u



class ModuleHomeTests(TestCase):
    def _user(self, *groups):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        u = get_user_model().objects.create_user(username="m"+groups[0] if groups else "mx", password="x")
        for g in groups:
            u.groups.add(Group.objects.get_or_create(name=g)[0])
        return u

    def test_stale_scanner_cookie_does_not_hijack_hub(self):
        # Regresja: ciasteczko pv_scanner (apka dzieli je z kartami przeglądarki) przerzucało
        # desktop z huba prosto do /control/. Hub renderuje kafelki i sprząta ciasteczko.
        from ui.roles import GROUP_ADMIN
        self.client.force_login(self._user(GROUP_ADMIN))
        self.client.cookies["pv_scanner"] = "1"
        resp = self.client.get(reverse("ui:home"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "ui/home.html")
        self.assertEqual(resp.cookies["pv_scanner"]["max-age"], 0)

    def test_hub_bounces_only_installed_app_window_client_side(self):
        # Apkę rozpoznaje przeglądarka per okno (pvInstalledApp) i odbija do launchera.
        from ui.roles import GROUP_ADMIN
        self.client.force_login(self._user(GROUP_ADMIN))
        resp = self.client.get(reverse("ui:home"))
        self.assertContains(resp, "pvInstalledApp()")
        self.assertContains(resp, 'location.replace("%s")' % reverse("ui:scanner_launcher"))

    def test_menu_app_param_marks_window_not_cookie(self):
        from ui.roles import GROUP_CONTROLLER
        self.client.force_login(self._user(GROUP_CONTROLLER))
        resp = self.client.get(reverse("ui:hu_control_menu") + "?app=1")
        self.assertNotIn("pv_scanner", resp.cookies)
        self.assertContains(resp, "sessionStorage.setItem('pvScannerApp'")

    def test_admin_sees_module_picker(self):
        from ui.roles import GROUP_ADMIN
        self.client.force_login(self._user(GROUP_ADMIN))
        resp = self.client.get(reverse("ui:home"))
        self.assertEqual(resp.status_code, 200)
        labels = {m["label"] for m in resp.context["modules"]}
        # GROOVE hub shows the platform modules ("tabs"). Admin reaches customers inside
        # Data Center, so the standalone "Baza klientów" tile is not duplicated here.
        self.assertEqual(labels, {"Data Center", "Paletyzacja", "Wycena przesyłek",
                                  "Magazyn 3D", "Kontrola HU", "Wydruk HU",
                                  "Zadania i powiadomienia", "ZARIA",
                                  "Wysyłka UKRAINA", "MATinfo", "Optymalizacja kartonów"})

    def test_single_module_user_redirected(self):
        from ui.roles import GROUP_CONTROLLER
        self.client.force_login(self._user(GROUP_CONTROLLER))
        resp = self.client.get(reverse("ui:home"))
        self.assertRedirects(resp, reverse("ui:hu_my_shift"))
