"""Warstwa ORM części 1: zapis „Wykryj z EWM” + raport zgodności na prawdziwej próbce EWM."""
from django.test import SimpleTestCase, TestCase

from wh3d import ewm_service
from wh3d.ewm_compliance import compliance
from wh3d.models import (
    BayTemplate, LocationOverride, WarehouseLocationMaster, WarehouseLocationMasterBatch, WarehouseModel,
    WarehouseModelRack,
)
from wh3d.tests.ewm_sample import SAMPLE_N_BAYS, load_sample


def make_model_and_master(extra_codes=()):
    wm = WarehouseModel.objects.create(name="Logistyczna")
    for aisle, n in SAMPLE_N_BAYS.items():
        WarehouseModelRack.objects.create(model=wm, zone="B0", rack_id=aisle, n_bays=n, bay_width_cm=280)
    batch = WarehouseLocationMasterBatch.objects.create(name="EWM", is_active=True)
    pairs = load_sample() + [(c, "0010") for c in extra_codes]
    WarehouseLocationMaster.objects.bulk_create(
        [WarehouseLocationMaster(batch=batch, location_code=c, warehouse_type=t) for c, t in pairs])
    return wm, batch


class ServiceTests(TestCase):
    def test_detect_apply_and_compliance_is_100_percent(self):
        wm, batch = make_model_and_master(extra_codes=["B0-60-100A", "C9-01-100A"])
        created, rows, overrides = ewm_service.apply_proposal(wm, ewm_service.detect_for_model(wm, batch))
        self.assertEqual((created, rows), (18, 6))
        self.assertEqual(LocationOverride.objects.count(), overrides)
        rack = wm.racks.get(rack_id="08")
        self.assertEqual(rack.bay_numbers, "10-47,50")
        self.assertEqual(rack.template.name, "3 pal. · A X Y Z · 0052/0010")

        report = ewm_service.compliance_for_model(wm, batch)
        status = {a["aisle"]: a["status"] for a in report["aisles"]}
        self.assertEqual(status, {"07": "ok", "08": "ok", "34": "ok", "38": "ok", "48": "ok", "54": "ok",
                                  "60": "no_row"})                   # strefa C9 poza modelem — pominięta
        self.assertEqual(report["summary"]["ok"], 6)
        self.assertEqual(report["summary"]["matched"], 3327)
        self.assertEqual(report["summary"]["pct"], 100.0)
        self.assertEqual((report["summary"]["ewm_outside"], report["summary"]["outside_aisles"]), (1, 1))

    def test_second_detect_reuses_templates_and_replaces_overrides(self):
        wm, batch = make_model_and_master()
        first = ewm_service.apply_proposal(wm, ewm_service.detect_for_model(wm, batch))
        again = ewm_service.detect_for_model(wm, batch)
        self.assertTrue(all(t["key"].startswith("pk:") for t in again["templates"]))
        second = ewm_service.apply_proposal(wm, again)
        self.assertEqual(second[0], 0)
        self.assertEqual(second[2], first[2])
        self.assertEqual(BayTemplate.objects.count(), 18)
        self.assertEqual(LocationOverride.objects.count(), first[2])

    def test_rack_without_template_and_missing_master(self):
        wm, batch = make_model_and_master()
        report = ewm_service.compliance_for_model(wm, batch)
        self.assertEqual({a["status"] for a in report["aisles"]}, {"no_template"})
        report = ewm_service.compliance_for_model(wm, None)
        self.assertEqual(report["summary"]["ewm"], 0)

    def test_master_rows_filters_by_zone_and_active_master(self):
        wm, batch = make_model_and_master(extra_codes=["C9-01-100A"])
        WarehouseLocationMasterBatch.objects.create(name="stary", is_active=False)
        self.assertEqual(ewm_service.active_master(), batch)
        codes = [r[0] for r in ewm_service.master_rows(batch, {"B0"})]
        self.assertNotIn("C9-01-100A", codes)
        self.assertEqual(len(codes), 3327)
        self.assertEqual(ewm_service.master_rows(batch, set()), [])


class CompliancePureTests(SimpleTestCase):
    def test_statuses_and_percent(self):
        rows = [{"zone": "B0", "rack_id": "01", "has_template": True},
                {"zone": "B0", "rack_id": "02", "has_template": True}]
        locs = [{"zone": "B0", "aisle": "01", "code": c} for c in ("B0-01-100A", "B0-01-101A")]
        locs += [{"zone": "B0", "aisle": "02", "code": "B0-02-100A"}]
        dups = {"B0-02-100A": ["B0-02", "B0-02"]}
        ewm = ["B0-01-100A", "B0-01-102A", "B0-02-100A", "B0-03-100A", "B0-XX"]
        rep = compliance(rows, locs, dups, ewm)
        a = {x["aisle"]: x for x in rep["aisles"]}
        self.assertEqual(a["01"]["status"], "diff")
        self.assertEqual(a["01"]["plan_only"], ["B0-01-101A"])
        self.assertEqual(a["01"]["ewm_only"], ["B0-01-102A"])
        self.assertEqual(a["01"]["pct"], 33.3)
        self.assertEqual(a["02"]["status"], "diff")                  # duplikat
        self.assertEqual(a["02"]["duplicates"], ["B0-02-100A"])
        self.assertEqual(a["03"]["status"], "no_row")
        self.assertEqual(rep["summary"]["unparsed"], 1)
        self.assertEqual(rep["summary"]["pct"], 50.0)                 # tylko przejścia 01+02 (03 = no_row)
        self.assertEqual(rep["summary"]["ewm_outside"], 1)
