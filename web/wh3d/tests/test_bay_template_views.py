"""Ekran szablonów gniazd (CRUD, role) + kolumny szablon/numeracja/kierunek w edycji współrzędnych."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from wh3d.models import BayTemplate, LocationOverride, WarehouseModel, WarehouseModelRack


def level_post(rows):
    data = {}
    for i, (letter, height, typ, split, kg) in enumerate(rows):
        data.update({f"lvl-{i}-letter": letter, f"lvl-{i}-height": height, f"lvl-{i}-type": typ,
                     f"lvl-{i}-split": split, f"lvl-{i}-kg": kg})
    return data


class BayTemplateViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="adm", password="x")
        self.viewer = User.objects.create_user(username="podglad", password="x")
        self.viewer.groups.add(Group.objects.get_or_create(name="Podgląd")[0])

    def test_list_for_viewer_without_edit_buttons(self):
        BayTemplate.objects.create(name="3 pal. · A X", pallets_per_beam=3,
                                   levels=[{"letter": "A", "height_mm": 1500, "ewm_type": "0052", "split": False}])
        self.client.force_login(self.viewer)
        r = self.client.get(reverse("ui:bay_template_list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "3 pal. · A X")
        self.assertNotContains(r, reverse("ui:bay_template_new"))
        self.assertEqual(self.client.get(reverse("ui:bay_template_new")).status_code, 403)

    def test_create_with_split_level(self):
        self.client.force_login(self.admin)
        post = {"name": "Kompletacja", "pallets_per_beam": "3", "beam_mm": "2700", "depth_mm": "1100", "notes": ""}
        post.update(level_post([("b", "400", "0052", "0", "300"), ("C", "400", "0052", "1", "300"),
                                ("", "", "", "0", "")]))
        r = self.client.post(reverse("ui:bay_template_new"), post)
        self.assertRedirects(r, reverse("ui:bay_template_list"))
        t = BayTemplate.objects.get(name="Kompletacja")
        self.assertEqual(t.level_label, "B C½")
        self.assertEqual(t.levels[1], {"letter": "C", "height_mm": 400, "ewm_type": "0052", "split": True,
                                       "max_kg": 300})

    def test_beam_mm_overflow_value_is_clamped_not_500(self):
        self.client.force_login(self.admin)
        post = {"name": "Nieskończoność", "pallets_per_beam": "3", "beam_mm": "inf", "depth_mm": "1100",
                "notes": ""}
        post.update(level_post([("A", "400", "0052", "0", "300"), ("", "", "", "0", "")]))
        r = self.client.post(reverse("ui:bay_template_new"), post)
        self.assertRedirects(r, reverse("ui:bay_template_list"))
        self.assertEqual(BayTemplate.objects.get(name="Nieskończoność").beam_mm, 0)

    def test_invalid_levels_show_error_and_save_nothing(self):
        self.client.force_login(self.admin)
        post = {"name": "Zły", "pallets_per_beam": "3", "beam_mm": "2700", "depth_mm": "1100"}
        post.update(level_post([("A", "400", "", "0", ""), ("A", "400", "", "0", "")]))
        r = self.client.post(reverse("ui:bay_template_new"), post)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "unikalne")
        self.assertFalse(BayTemplate.objects.filter(name="Zły").exists())

    def test_delete_protected_template_keeps_it(self):
        t = BayTemplate.objects.create(name="Przejazd", pallets_per_beam=4,
                                       levels=[{"letter": "Y", "height_mm": 1800, "ewm_type": "", "split": False}])
        wm = WarehouseModel.objects.create(name="M")
        rack = WarehouseModelRack.objects.create(model=wm, zone="B0", rack_id="07")
        LocationOverride.objects.create(rack=rack, bay=29, action="template", template=t)
        self.client.force_login(self.admin)
        r = self.client.post(reverse("ui:bay_template_delete", args=[t.pk]), follow=True)
        self.assertContains(r, "jest użyty")
        self.assertTrue(BayTemplate.objects.filter(pk=t.pk).exists())


class CoordsRuleColumnsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="adm", password="x")
        self.client.force_login(self.admin)
        self.wm = WarehouseModel.objects.create(name="M")
        self.rack = WarehouseModelRack.objects.create(model=self.wm, zone="B0", rack_id="07", n_bays=39)
        self.tpl = BayTemplate.objects.create(name="T", pallets_per_beam=3,
                                              levels=[{"letter": "A", "height_mm": 1500, "ewm_type": "", "split": False}])

    def post(self, **extra):
        p = f"rack_{self.rack.pk}_"
        data = {p + "x_m": "1", p + "y_m": "2", p + "angle_deg": "0", p + "bay_width_cm": "280",
                p + "depth_cm": "103", p + "level_height_cm": "200"}
        data.update({p + k: v for k, v in extra.items()})
        return self.client.post(reverse("ui:warehouse_model_coords", args=[self.wm.pk]), data, follow=True)

    def test_get_shows_template_select(self):
        r = self.client.get(reverse("ui:warehouse_model_coords", args=[self.wm.pk]))
        self.assertContains(r, f'name="rack_{self.rack.pk}_template"')
        self.assertContains(r, f'name="rack_{self.rack.pk}_bay_numbers"')

    def test_saves_template_numbering_and_reverse(self):
        self.post(template=str(self.tpl.pk), bay_numbers="10-47,50", reverse="1")
        self.rack.refresh_from_db()
        self.assertEqual((self.rack.template, self.rack.bay_numbers, self.rack.reverse), (self.tpl, "10-47,50", True))

    def test_invalid_numbering_keeps_old_value_and_warns(self):
        self.rack.bay_numbers = "10-48"
        self.rack.save()
        r = self.post(template="", bay_numbers="48-10")
        self.rack.refresh_from_db()
        self.assertEqual(self.rack.bay_numbers, "10-48")
        self.assertIsNone(self.rack.template)
        self.assertContains(r, "Zakres malejący")

    def test_old_form_without_new_fields_keeps_rule(self):
        self.rack.template, self.rack.bay_numbers, self.rack.reverse = self.tpl, "10-48", True
        self.rack.save()
        self.post()
        self.rack.refresh_from_db()
        self.assertEqual((self.rack.template, self.rack.bay_numbers, self.rack.reverse), (self.tpl, "10-48", True))
