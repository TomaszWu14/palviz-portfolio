"""Smoke test: pk-based detail/edit/view pages render (no 500) with real minimal data."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from ui import models as m


class SmokeDetailTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})

    def test_detail_and_edit_pages(self):
        cat = m.ProductCategory.objects.create(name="Kat", code="KAT")
        prod = m.Product.objects.create(code="P1", name="Produkt 1", category=cat,
                                        unit_length_cm=10, unit_width_cm=10, unit_height_cm=10)
        inner = m.InnerPack.objects.create(name="IP", length_cm=20, width_cm=15, height_cm=10)
        carton = m.Carton.objects.create(name="K", length_cm=40, width_cm=30, height_cm=20, unit_weight_kg=2.0)
        instr = m.PalletizationInstruction.objects.create(
            product=prod, carton=carton, carton_l=40, carton_w=30, carton_h=20,
            unit_weight=2.0, pcs_per_carton=10, demand_pcs=500)
        report = m.ErrorReport.objects.create(description="coś nie tak", product_code="P1", instruction=instr)
        loc = m.WarehouseLocationType.objects.create(name="L1", width_cm=80, depth_cm=120, total_height_cm=180)
        snap = m.WarehouseSnapshot.objects.create(name="Snap")
        wm = m.WarehouseModel.objects.create(name="WM")
        m.WarehouseModelRack.objects.create(model=wm, zone="B0", rack_id="01")
        carrier = m.Carrier.objects.create(name="DHL")
        zone = m.CarrierZone.objects.create(carrier=carrier, name="PL")
        m.CarrierRate.objects.create(zone=zone, weight_from_kg=0, price_eur=10)
        ship = m.Shipment.objects.create(name="S1")
        m.ShipmentLine.objects.create(shipment=ship, product=prod, quantity=5)
        rtype = m.WarehouseRackType.objects.create(code="RT", name="Typ")
        pab = m.PickerActivityBatch.objects.create(name="PA")
        batch = m.Batch.objects.create(name="B1")
        pal = m.Palletization.objects.create(batch=batch, sku="P1", carton_l=40, carton_w=30,
                                             carton_h=20, unit_weight=2.0, pcs_per_carton=10, demand_pcs=500)

        targets = [
            ("ui:planner_product_edit", {"pk": prod.pk}),
            ("ui:planner_product_hierarchy", {"pk": prod.pk}),
            ("ui:planner_carton_edit", {"pk": carton.pk}),
            ("ui:planner_inner_pack_edit", {"pk": inner.pk}),
            ("ui:planner_instruction_detail", {"pk": instr.pk}),
            ("ui:planner_instruction_panel", {"pk": instr.pk}),
            ("ui:planner_instruction_excel", {"pk": instr.pk}),
            ("ui:planner_report_detail", {"pk": report.pk}),
            ("ui:planner_location_edit", {"pk": loc.pk}),
            ("ui:planner_location_simulate", {"pk": loc.pk}),
            ("ui:warehouse_model_view", {"pk": wm.pk}),
            ("ui:warehouse_model_coords", {"pk": wm.pk}),
            ("ui:warehouse_model_delete", {"pk": wm.pk}),
            ("ui:warehouse_map_detail", {"pk": snap.pk}),
            ("ui:planner_shipment_detail", {"pk": ship.pk}),
            ("ui:planner_shipment_edit", {"pk": ship.pk}),
            ("ui:planner_carrier_edit", {"pk": carrier.pk}),
            ("ui:planner_carrier_rates", {"pk": carrier.pk}),
            ("ui:heatmap_detail", {"pk": pab.pk}),
            ("ui:warehouse_rack_type_edit", {"pk": rtype.pk}),
            ("ui:pallet_detail", {"pid": pal.pk}),
            ("ui:pallet_panel", {"pid": pal.pk}),
            ("ui:batch_detail", {"batch_id": batch.pk}),
            ("ui:pallet_custom_editor", {"pk": instr.pk}),
        ]
        failures = []
        for name, kw in targets:
            try:
                url = reverse(name, kwargs=kw)
            except Exception as e:
                failures.append((name, f"reverse-fail {e}")); continue
            try:
                resp = self.client.get(url)
            except Exception as e:
                failures.append((name, f"{type(e).__name__}: {e}")); continue
            if resp.status_code >= 500:
                failures.append((name, resp.status_code))
        if failures:
            self.fail(f"{len(failures)} page(s) failed:\n" + "\n".join(f"  {n} -> {s}" for n, s in failures))

    def test_instruction_edit_shows_edit_labels(self):
        prod = m.Product.objects.create(code="PX", name="X")
        instr = m.PalletizationInstruction.objects.create(
            product=prod, carton_l=40, carton_w=30, carton_h=20,
            unit_weight=2.0, pcs_per_carton=10, demand_pcs=500)
        from django.urls import reverse
        r = self.client.get(reverse("ui:planner_instruction_edit", kwargs={"pk": instr.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Edytuj instrukcję")      # not "Nowa instrukcja"
        self.assertNotContains(r, "Nowa instrukcja")

    def test_pallet_custom_editor_exposes_inline_handlers(self):
        # regression: handlers lived in an IIFE → inline onclick="saveAll()" = ReferenceError
        prod = m.Product.objects.create(code="PZ", name="Z")
        instr = m.PalletizationInstruction.objects.create(
            product=prod, carton_l=40, carton_w=30, carton_h=20,
            unit_weight=2.0, pcs_per_carton=10, demand_pcs=500)
        from django.urls import reverse
        r = self.client.get(reverse("ui:pallet_custom_editor", kwargs={"pk": instr.pk}))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("Object.assign(window", html)
        for fn in ("saveAll", "addLayer", "setTool", "canvasClick"):
            self.assertIn(fn, html)

    def test_calc_upload_error_keeps_product_dropdown(self):
        m.Product.objects.create(code="PDROP", name="D")
        # invalid upload (no file) → error render must still include products
        r = self.client.post("/planner/calc/upload/", {})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "PDROP")
