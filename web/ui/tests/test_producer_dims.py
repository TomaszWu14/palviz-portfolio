from django.test import SimpleTestCase

from ui.producer_dims import compare_dims, parse_dim, supplier_from_filename


class CompareDimsTests(SimpleTestCase):
    def test_equal_after_transposition_is_ok(self):
        # sorted-triple: axis order irrelevant
        v, delta = compare_dims((53.5, 41, 37), (37, 53.5, 41))
        self.assertEqual(v, "ok")
        self.assertEqual(delta, 0.0)

    def test_beyond_tolerance_is_mismatch(self):
        v, delta = compare_dims((60, 40, 30), (60, 40, 36))   # 6 cm on one axis
        self.assertEqual(v, "mismatch")
        self.assertAlmostEqual(delta, 6.0)

    def test_small_box_protected_by_1cm_floor(self):
        # 8 cm axis, +0.6 cm < max(1.0, 5%*8=0.4) = 1.0 → ok
        v, _ = compare_dims((8, 8, 5.5), (8.6, 8, 5.5))
        self.assertEqual(v, "ok")

    def test_large_carton_within_percent_is_ok(self):
        # 60 cm axis, +2 cm < max(1.0, 5%*60=3.0) = 3.0 → ok
        v, _ = compare_dims((60, 40, 30), (62, 40, 30))
        self.assertEqual(v, "ok")

    def test_missing_side_is_no_data(self):
        self.assertEqual(compare_dims((None, 40, 30), (60, 40, 30))[0], "no_data")
        self.assertEqual(compare_dims((60, 40, 30), None)[0], "no_data")

    def test_parse_dim(self):
        self.assertEqual(parse_dim(43.5), 43.5)
        self.assertEqual(parse_dim("52"), 52.0)
        self.assertIsNone(parse_dim("-"))
        self.assertIsNone(parse_dim(None))

    def test_supplier_from_filename(self):
        self.assertEqual(supplier_from_filename("INTCO GLOVES packaging size.xlsx"),
                         "INTCO GLOVES")
        self.assertEqual(supplier_from_filename("packaging size - BAIHE.xlsx"), "BAIHE")


import io

from django.test import TestCase
import openpyxl

from ui.models import (ProducerCartonBatch, ProducerCartonDim, Product,
                       PalletizationInstruction, Task)
from ui.views.producer_dims import _ingest_file, _raise_mismatch_tasks


def _mk_xlsx(rows):
    """Buduje plik jak szablon 'dane opakowań': 3 wiersze nagłówka (REF w kol B wiersza 3),
    dane od wiersza 4. rows = [(ref, l, w, h), ...]."""
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "dane opakowań"
    ws.append([None] * 14)                                   # r1
    ws.append([None, None, None, None, None, None, None, None, None, None, None, None, None, None])  # r2
    hdr = [None] * 14; hdr[1] = "REF"; ws.append(hdr)        # r3 (idx2): kol B = REF
    for i, (ref, l, w, h) in enumerate(rows, 1):
        row = [None] * 14
        row[0], row[1] = i, ref
        row[8], row[9], row[10] = l, w, h
        ws.append(row)
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    buf.name = "TESTSUP packaging size.xlsx"
    return buf


class ProducerDimsImportTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_superuser("md", "m@m.pl", "x")
        p = Product.objects.create(code="AF-6090", name="Podkład")
        PalletizationInstruction.objects.create(
            product=p, version=1, is_active=True, variant="A",
            carton_l=53, carton_w=41, carton_h=37,
            unit_weight=1.0, pcs_per_carton=10, carton_tare=0.5)

    def test_ingest_creates_batch_rows_and_resolves_product(self):
        f = _mk_xlsx([("AF-6090", 53, 41, 37), ("UNKNOWN-REF", 20, 20, 20)])
        batch = _ingest_file(f, self.user)
        self.assertEqual(batch.supplier, "TESTSUP")
        self.assertEqual(batch.row_count, 2)
        got = {r.ref_code: r for r in ProducerCartonDim.objects.all()}
        self.assertIsNotNone(got["AF-6090"].product)
        self.assertIsNone(got["UNKNOWN-REF"].product)

    def test_reimport_deactivates_previous_batch(self):
        _ingest_file(_mk_xlsx([("AF-6090", 53, 41, 37)]), self.user)
        _ingest_file(_mk_xlsx([("AF-6090", 53, 41, 37)]), self.user)
        active = ProducerCartonBatch.objects.filter(supplier="TESTSUP", is_active=True)
        self.assertEqual(active.count(), 1)

    def test_bad_header_rejected(self):
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "dane opakowań"
        ws.append(["nonsense"]); ws.append([None]); ws.append([None]); ws.append([1])
        buf = io.BytesIO(); wb.save(buf); buf.seek(0); buf.name = "X packaging size.xlsx"
        with self.assertRaises(ValueError):
            _ingest_file(buf, self.user)

    def test_mismatch_creates_one_deduped_task(self):
        # producent 53x41x50 vs nasze 53x41x37 → rozjazd 13 cm
        batch = _ingest_file(_mk_xlsx([("AF-6090", 53, 41, 50)]), self.user)
        n = _raise_mismatch_tasks(batch)
        self.assertEqual(n, 1)
        self.assertEqual(Task.objects.filter(category="carton_dim_mismatch").count(), 1)
        # reimport → dedup, brak nowego taska
        batch2 = _ingest_file(_mk_xlsx([("AF-6090", 53, 41, 50)]), self.user)
        self.assertEqual(_raise_mismatch_tasks(batch2), 0)

    def test_ok_and_nodata_create_no_task(self):
        batch = _ingest_file(_mk_xlsx([("AF-6090", 53, 41, 37),      # ok
                                       ("UNKNOWN-REF", 20, 20, 20)]),  # no_data (brak product)
                             self.user)
        self.assertEqual(_raise_mismatch_tasks(batch), 0)


class ProducerDimsReportTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_superuser("md2", "m2@m.pl", "x")
        p = Product.objects.create(code="AF-6090", name="Podkład")
        PalletizationInstruction.objects.create(
            product=p, version=1, is_active=True, variant="A",
            carton_l=53, carton_w=41, carton_h=37,
            unit_weight=1.0, pcs_per_carton=10, carton_tare=0.5)
        _ingest_file(_mk_xlsx([("AF-6090", 53, 41, 50)]), self.user)   # mismatch row
        self.client.force_login(self.user)

    def test_report_renders_with_verdict_badge(self):
        from django.urls import reverse
        resp = self.client.get(reverse("ui:producer_dims"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["counts"]["mismatch"], 1)
        self.assertContains(resp, "AF-6090")
