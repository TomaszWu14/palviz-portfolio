"""B-015: link zadania bez `source_ref` ma jako treść samą strzałkę „→” — brak nazwy dostępnej
(czytnik ekranu czyta „strzałka w prawo”) i cel dotyku 10 px szerokości. Naprawiony: domyślnie „Otwórz →”."""
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ui.models import Task


class TaskLinkNameTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("su_b015", password="x"))

    def test_link_without_source_ref_has_readable_name(self):
        Task.objects.create(title="Rozbieżność stanu", url="/control/hu/1/", source_ref="")
        html = self.client.get(reverse("ui:tasks_home")).content.decode()
        link = re.search(r'<a class="text-sm" href="/control/hu/1/">(.*?)</a>', html, re.S)
        self.assertIsNotNone(link)
        self.assertRegex(link.group(1).replace("→", ""), r"\w", "treść linku to sama strzałka „→”")
