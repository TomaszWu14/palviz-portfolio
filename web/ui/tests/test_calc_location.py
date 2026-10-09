"""Calculator binds target height to a chosen WarehouseLocationType."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from ui.forms import ManualForm
from ui.models import WarehouseLocationType
from ui.views import _form_max_height


class CalcLocationTests(TestCase):
    def _data(self, **over):
        d = dict(pallet="EU", max_height_total="215", max_weight="1000", sku="S", variant="STD",
                 carton_l="40", carton_w="30", carton_h="25", unit_weight="0.45",
                 pcs_per_carton="24", demand_pcs="100", carton_tare="0.2", render_layers="3")
        d.update(over)
        return d

    def test_location_overrides_height(self):
        loc = WarehouseLocationType.objects.create(name="Reg A", width_cm=120, depth_cm=80,
                                                   total_height_cm=185)
        form = ManualForm(self._data(location=str(loc.pk)))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(_form_max_height(form), 185)        # from location, not 215

    def test_no_location_uses_manual_height(self):
        form = ManualForm(self._data(max_height_total="240"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(_form_max_height(form), 240)

    def test_calc_index_renders_location_dropdown(self):
        WarehouseLocationType.objects.create(name="Reg B", width_cm=120, depth_cm=80, total_height_cm=200)
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})
        r = self.client.get("/planner/calc/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Reg B")
        self.assertContains(r, 'id="loc_heights"')
