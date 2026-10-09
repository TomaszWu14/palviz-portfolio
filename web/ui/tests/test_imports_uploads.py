"""Sort po priorytecie na liście HU + importy: użytkownicy (upsert po loginie) i
rozszerzone kolumny odbiorców (kategoria / przewoźnik / standardowa wysokość)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import Shipment, HandlingUnit, Customer
from ui.roles import GROUP_ADMIN, GROUP_CONTROLLER, GROUP_MASTER_DATA


def _admin(name="adm"):
    u = get_user_model().objects.create_user(username=name, password="x", is_superuser=True)
    u.groups.add(Group.objects.get_or_create(name=GROUP_ADMIN)[0])
    return u


class PrioritySort(TestCase):
    def test_priority_hu_first(self):
        sh = Shipment.objects.create(name="D")
        HandlingUnit.objects.create(shipment=sh, seq=1, code="HU-N", is_priority=False)
        HandlingUnit.objects.create(shipment=sh, seq=2, code="HU-P", is_priority=True)
        self.client.force_login(_admin())
        r = self.client.get(reverse("ui:planner_stock_contents"), {"view": "hu", "sort": "priority"})
        self.assertEqual(r.status_code, 200)
        page = list(r.context["page_obj"])
        self.assertEqual(page[0].code, "HU-P")   # pilna przed zwykłą


class ControlStatusCounters(TestCase):
    """Wymiar „kontrola" jest rozdzielony od „kompletacja (picking)": chipy statusów
    kontroli z licznikami + filtr ?status= niezależny od tabów is_completed."""

    def test_counts_and_filter_independent_of_picking_tab(self):
        sh = Shipment.objects.create(name="D")
        HandlingUnit.objects.create(shipment=sh, seq=1, code="H1", status="ok",
                                    is_completed=True, verified_at="2026-08-01T10:00Z")
        HandlingUnit.objects.create(shipment=sh, seq=2, code="H2", status="planned",
                                    is_completed=True)
        HandlingUnit.objects.create(shipment=sh, seq=3, code="H3", status="planned",
                                    is_completed=False)
        self.client.force_login(_admin("adm2"))
        r = self.client.get(reverse("ui:planner_stock_contents"), {"view": "hu"})
        counts = {key: n for key, _, n in r.context["ctrl_counts"]}
        self.assertEqual(counts["ok"], 1)
        self.assertEqual(counts["planned"], 2)
        # Filtr kontroli działa w obrębie taba pickingu (skompletowane + planned → tylko H2).
        r = self.client.get(reverse("ui:planner_stock_contents"),
                            {"view": "hu", "tab": "completed", "status": "planned"})
        self.assertEqual([h.code for h in r.context["page_obj"]], ["H2"])


class UsersImport(TestCase):
    def _post(self, csv_text):
        f = SimpleUploadedFile("u.csv", csv_text.encode("utf-8"), content_type="text/csv")
        return self.client.post(reverse("ui:admin_users_import"), {"file": f})

    def test_creates_user_with_role_and_profile(self):
        self.client.force_login(_admin())
        self._post("login,imię,nazwisko,email,hasło,role,telefon,dział,aktywny\n"
                   "jkowalski,Jan,Kowalski,j@x.pl,Haslo123!,Kontrola HU,+48 600 1,Magazyn,tak\n")
        u = get_user_model().objects.get(username="jkowalski")
        self.assertEqual(u.first_name, "Jan")
        self.assertTrue(u.groups.filter(name=GROUP_CONTROLLER).exists())
        self.assertTrue(u.check_password("Haslo123!"))
        self.assertEqual(u.profile.department, "Magazyn")

    def test_reimport_updates_not_duplicates(self):
        self.client.force_login(_admin())
        self._post("login,email\njkowalski,old@x.pl\n")
        self._post("login,email\njkowalski,new@x.pl\n")
        users = get_user_model().objects.filter(username="jkowalski")
        self.assertEqual(users.count(), 1)
        self.assertEqual(users.first().email, "new@x.pl")

    def test_unknown_role_ignored(self):
        self.client.force_login(_admin())
        self._post("login,role\nanowak,\"Kontrola HU, NieistniejacaRola\"\n")
        u = get_user_model().objects.get(username="anowak")
        self.assertEqual({g.name for g in u.groups.all()}, {GROUP_CONTROLLER})


class CustomerImportExtras(TestCase):
    def test_category_carrier_height_imported(self):
        u = get_user_model().objects.create_user(username="md", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)
        csv = ("Klient,Nazwa 1,Kraj,Miasto,Kategoria,Przewoźnik,Wysokość [cm]\n"
               "100245,Szpital Przykładowy,UA,Lwów,VIP,GLS,160\n"
               "100310,Klinika Demo,PL,Warszawa,Delta,GEIS,180\n")
        f = SimpleUploadedFile("k.csv", csv.encode("utf-8"), content_type="text/csv")
        self.client.post(reverse("ui:planner_customer_import"), {"file": f})
        c1 = Customer.objects.get(code="100245")
        self.assertEqual(c1.category, "vip")
        self.assertTrue(c1.is_vip)               # kategoria VIP ustawia flagę
        self.assertEqual(c1.carrier, "GLS")
        self.assertEqual(c1.max_pallet_height_cm, 160)
        c2 = Customer.objects.get(code="100310")
        self.assertEqual(c2.category, "delta")
        self.assertFalse(c2.is_vip)
