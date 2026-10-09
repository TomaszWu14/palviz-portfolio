"""pickHU = jedna fizyczna paleta: globalna unikalność kodu i jednoznaczne skanowanie.

Wcześniej import kluczował HU po (dostawa, pickHU), więc ten sam kod mógł istnieć
w dwóch dostawach, a skaner (`_resolve_hu`) brał `.first()` — kontroler trafiał
w losową z nich."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from ui.models import HandlingUnit, Shipment
from ui.roles import ALL_GROUPS
from huctl.views.hu_control import _resolve_hu


def _user():
    u = get_user_model().objects.create_user(username="uniq", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class HUCodeUniqueTests(TestCase):
    def test_duplicate_code_rejected_by_db(self):
        a = Shipment.objects.create(name="D1")
        b = Shipment.objects.create(name="D2")
        HandlingUnit.objects.create(shipment=a, seq=1, code="HU001")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HandlingUnit.objects.create(shipment=b, seq=1, code="HU001")

    def test_blank_codes_are_exempt(self):
        # HU wygenerowane z paletyzacji nie mają jeszcze etykiety SAP — pustych może być wiele.
        sh = Shipment.objects.create(name="D1")
        HandlingUnit.objects.create(shipment=sh, seq=1, code="")
        HandlingUnit.objects.create(shipment=sh, seq=2, code="")
        self.assertEqual(sh.handling_units.filter(code="").count(), 2)


class HUResolveTests(TestCase):
    def test_case_insensitive_fallback_refuses_ambiguous_match(self):
        # Constraint jest case-sensitive, więc 'ABC'/'abc' mogą współistnieć. Skaner
        # ma nie zgadywać — woli nie znaleźć nic niż otworzyć losową paletę.
        sh = Shipment.objects.create(name="D1")
        HandlingUnit.objects.create(shipment=sh, seq=1, code="ABC")
        HandlingUnit.objects.create(shipment=sh, seq=2, code="abc")
        self.assertIsNone(_resolve_hu("AbC"))

    def test_case_insensitive_fallback_still_works_when_unambiguous(self):
        sh = Shipment.objects.create(name="D1")
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="ABC")
        self.assertEqual(_resolve_hu("abc"), hu)


HEADER = ("Dostawa;pickHU;REF;Opis;LOT;Data ważności;Ilość;JM;"
          "Lokalizacja;Typ magazynu;Odbiorca\n")


class HUImportRepointTests(TestCase):
    def setUp(self):
        self.client.force_login(_user())

    def _upload(self, text):
        f = SimpleUploadedFile("hu.csv", text.encode("utf-8"), content_type="text/csv")
        return self.client.post(reverse("ui:planner_hu_import"), {"file": f})

    def test_same_pickhu_in_another_shipment_repoints_instead_of_duplicating(self):
        self._upload(HEADER + "D-AAA;HU777;RG-50;Rękawice;L1;2027-05-31;80;OP;A-1;WT01;X\n")
        hu = HandlingUnit.objects.get(code="HU777")
        hu.picker = "Kowalski"                  # ślad, że to ta sama, przepięta paleta
        hu.save(update_fields=["picker"])

        # SAP przeksięgował paletę na inną dostawę.
        self._upload(HEADER + "D-BBB;HU777;RG-50;Rękawice;L1;2027-05-31;80;OP;A-1;WT01;X\n")

        self.assertEqual(HandlingUnit.objects.filter(code="HU777").count(), 1)
        hu.refresh_from_db()
        self.assertEqual(hu.shipment.name, "D-BBB")
        self.assertEqual(hu.picker, "Kowalski")
        self.assertEqual(hu.items.count(), 1)

    def test_repointed_hu_keeps_control_history(self):
        self._upload(HEADER + "D-AAA;HU778;RG-50;Rękawice;L1;2027-05-31;80;OP;A-1;WT01;X\n")
        hu = HandlingUnit.objects.get(code="HU778")
        pk = hu.pk
        self._upload(HEADER + "D-BBB;HU778;RG-50;Rękawice;L1;2027-05-31;80;OP;A-1;WT01;X\n")
        # Ten sam wiersz, więc wszystko co na nim wisi (próby, zgłoszenia) przetrwało.
        self.assertEqual(HandlingUnit.objects.get(code="HU778").pk, pk)

    def test_repoint_does_not_collide_with_existing_seq(self):
        # Dostawa docelowa ma już HU o seq=1 — przepięcie musi dostać wolny numer.
        self._upload(HEADER
                     + "D-BBB;HU100;RG-50;R;L1;2027-05-31;10;OP;A-1;WT01;X\n"
                     + "D-AAA;HU200;RG-50;R;L1;2027-05-31;10;OP;A-1;WT01;X\n")
        self._upload(HEADER + "D-BBB;HU200;RG-50;R;L1;2027-05-31;10;OP;A-1;WT01;X\n")
        b = Shipment.objects.get(name="D-BBB")
        self.assertEqual(b.handling_units.count(), 2)
        seqs = sorted(b.handling_units.values_list("seq", flat=True))
        self.assertEqual(len(set(seqs)), 2)
