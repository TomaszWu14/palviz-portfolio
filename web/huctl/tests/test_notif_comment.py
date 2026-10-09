"""Regression: the notification-alert partial's comment must NOT leak as literal text.
A multi-line `{# #}` renders verbatim (Django strips only single-line ones), so it was
showing on every scanner page. It's now a `{% comment %}` block."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.roles import GROUP_CONTROLLER


class NotifCommentTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="ctrl", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        self.client.force_login(u)

    def test_comment_not_leaked_on_control_menu(self):
        html = self.client.get(reverse("ui:hu_control_menu")).content.decode()
        self.assertNotIn("Alert nowej wiadomości", html)   # komentarz nie wycieka jako tekst
        self.assertNotIn("{# ", html)
