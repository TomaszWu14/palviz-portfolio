"""Formularz zgłoszenia MatInfo zależny od typu: walidacja per typ, wycofany
„carton_too_heavy", nowy „change_location" (lista lokalizacji → Master Data)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ui.models import Product, PackagingIssue, Notification
from ui.roles import GROUP_WAREHOUSE, GROUP_MASTER_DATA
from ui.views.phv import LOCATION_SUGGESTIONS


def _wh():
    u = get_user_model().objects.create_user(username="mag", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
    return u


class ReportPerTypeTest(TestCase):
    def setUp(self):
        self.u = _wh()
        self.client.force_login(self.u)
        Product.objects.create(code="REF-1", name="X")

    def _post(self, **data):
        data.setdefault("ref_code", "REF-1")
        return self.client.post(reverse("ui:phv_report"), data)

    def test_wrong_name_stores_names_no_quantity_required(self):
        self._post(issue_type="wrong_name", current_value="Stara", correct_value="Nowa nazwa")
        i = PackagingIssue.objects.get()
        self.assertEqual(i.issue_type, "wrong_name")
        self.assertEqual(i.correct_value, "Nowa nazwa")     # zapis bez pól ilości

    def test_change_location_valid_code_stores_label_and_notifies_master_data(self):
        md = get_user_model().objects.create_user(username="md", password="x")
        md.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        code = LOCATION_SUGGESTIONS[0][0]                   # "A-220"
        self._post(issue_type="change_location", correct_value=code)
        i = PackagingIssue.objects.get()
        self.assertEqual(i.issue_type, "change_location")
        self.assertEqual(i.correct_value, LOCATION_SUGGESTIONS[0][1])   # etykieta, nie kod
        self.assertTrue(Notification.objects.filter(recipient=md).exists())  # → Master Data

    def test_change_location_bad_code_rejected(self):
        self._post(issue_type="change_location", correct_value="NIE-MA")
        self.assertEqual(PackagingIssue.objects.count(), 0)

    def test_missing_render_requires_photo(self):
        # Zgłoszenie „brak renderu" = fotodokumentacja: bez zdjęcia odrzucone.
        self._post(issue_type="missing_render", description="brak zdjęcia w bazie")
        self.assertEqual(PackagingIssue.objects.filter(issue_type="missing_render").count(), 0)
        from django.core.files.uploadedfile import SimpleUploadedFile
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (2, 2)).save(buf, "PNG")     # realny obraz — _valid_image weryfikuje PIL-em
        png = buf.getvalue()
        self._post(issue_type="missing_render", description="foto w załączniku",
                   photo=SimpleUploadedFile("ju.png", png, content_type="image/png"))
        issue = PackagingIssue.objects.get(issue_type="missing_render")
        self.assertTrue(issue.photo)

    def test_other_requires_description(self):
        self._post(issue_type="other", description="")
        self.assertEqual(PackagingIssue.objects.count(), 0)
        self._post(issue_type="other", description="coś nie tak")
        self.assertEqual(PackagingIssue.objects.count(), 1)

    def test_missing_process_requires_valid_process(self):
        self._post(issue_type="missing_process", correct_value="9999")
        self.assertEqual(PackagingIssue.objects.count(), 0)
        self._post(issue_type="missing_process", correct_value="0050")
        self.assertEqual(PackagingIssue.objects.get().correct_value, "0050")

    def test_retired_type_rejected(self):
        self._post(issue_type="carton_too_heavy", description="x")
        self.assertEqual(PackagingIssue.objects.count(), 0)

    def test_picker_excludes_retired_includes_change_location(self):
        r = self.client.get(reverse("ui:phv_home"), {"q": "REF-1"})
        picker = dict(r.context["issue_types"])
        self.assertNotIn("carton_too_heavy", picker)
        self.assertIn("change_location", picker)


class LegacyDisplayTest(SimpleTestCase):
    def test_retired_type_still_has_display_label(self):
        # Stare wiersze carton_too_heavy nadal renderują etykietę (wartość zostaje w TYPES).
        i = PackagingIssue(ref_code="R", issue_type="carton_too_heavy")
        self.assertEqual(i.get_issue_type_display(), "Karton za ciężki")
