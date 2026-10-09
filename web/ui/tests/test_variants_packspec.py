"""BLOK E — E2 warianty paletyzacji A/B + E3 packspec z walidacją MARM."""
import io

import openpyxl
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import (PackagingIssue, PackSpecBatch,
                       PalletizationInstruction, Product)
from ui.roles import GROUP_MASTER_DATA


def _md():
    u = get_user_model().objects.create_user(username="md-e", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
    return u


def _instr(p, version, variant="A", cpp=32, pcs=24):
    return PalletizationInstruction.objects.create(
        product=p, version=version, variant=variant, is_active=True,
        unit_weight=0.5, pcs_per_carton=pcs, carton_l=40, carton_w=30, carton_h=25,
        pallet_length_cm=120, pallet_width_cm=80, max_height_total_cm=215,
        pallet_base_height_cm=15,
        layouts=[{"name": "L1", "cartons_per_pallet": cpp}], selected_layout="L1")


class VariantTests(TestCase):
    def setUp(self):
        self.md = _md()
        self.p = Product.objects.create(code="VAR-A", name="Wariantowy")
        self.a = _instr(self.p, 1, "A", cpp=32)
        self.b = _instr(self.p, 2, "B", cpp=40)

    def test_default_variant_a_wins_despite_higher_b_version(self):
        """Domyślnie obowiązuje A — wyższa wersja w B NIE przejmuje karty."""
        self.assertEqual(self.p.latest_instruction(), self.a)
        self.assertTrue(self.p.has_variant_b())

    def test_switch_action_changes_effective_instruction_with_log(self):
        self.client.force_login(self.md)
        r = self.client.post(reverse("ui:product_switch_variant", args=[self.p.pk]))
        self.assertEqual(r.status_code, 302)
        self.p.refresh_from_db()
        self.assertEqual(self.p.active_variant, "B")
        self.assertEqual(self.p.latest_instruction(), self.b)
        # Log kto/kiedy: HistoricalRecords na Product.
        h = self.p.history.first()
        self.assertEqual(h.active_variant, "B")

    def test_switch_refused_without_target_instruction(self):
        solo = Product.objects.create(code="VAR-S", name="Tylko A")
        _instr(solo, 1, "A")
        self.client.force_login(self.md)
        self.client.post(reverse("ui:product_switch_variant", args=[solo.pk]))
        solo.refresh_from_db()
        self.assertEqual(solo.active_variant, "A")     # brak B → bez zmiany

    def test_fallback_when_active_variant_empty(self):
        """Bezpiecznik: produkt przełączony na B bez instrukcji B nie gaśnie."""
        solo = Product.objects.create(code="VAR-F", name="Fallback", active_variant="B")
        ia = _instr(solo, 1, "A")
        self.assertEqual(solo.latest_instruction(), ia)


def _xlsx(rows):
    wb = openpyxl.Workbook(); ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return SimpleUploadedFile("packspec.xlsx", buf.read())


class PackSpecTests(TestCase):
    def setUp(self):
        self.md = _md()
        self.client.force_login(self.md)
        self.p = Product.objects.create(code="PS-1", name="Packspec 1")
        _instr(self.p, 1, cpp=32, pcs=24)              # master: 24 szt/kar, 32 kar/pal

    def _upload(self, rows):
        return self.client.post(reverse("ui:packspec_upload"), {"file": _xlsx(rows)})

    def test_upload_and_validation_flags_mismatch(self):
        self._upload([
            ["REF", "Szt/karton", "Kartony/paleta"],
            ["PS-1", 24, 32],                          # zgodne
            ["PS-2", 10, 5],                           # brak w MD
        ])
        batch = PackSpecBatch.objects.get()
        self.assertEqual(batch.row_count, 2)
        r = self.client.get(reverse("ui:packspec_list"))
        by_ref = {row["spec"].ref_code: row for row in r.context["rows"]}
        self.assertEqual(by_ref["PS-1"]["diffs"], [])
        self.assertTrue(by_ref["PS-2"]["diffs"])       # brak instrukcji → rozbieżność

    def test_mismatch_alert_and_one_click_issue(self):
        self._upload([["REF", "Szt/karton"], ["PS-1", 30]])   # 30 ≠ 24
        r = self.client.get(reverse("ui:packspec_list"))
        row = r.context["rows"][0]
        self.assertIn("szt/karton: packspec 30 ≠ MARM 24", row["diffs"])
        self.assertContains(r, "Utwórz zgłoszenie")
        self.client.post(reverse("ui:packspec_report_mismatch", args=[row["spec"].pk]))
        issue = PackagingIssue.objects.get(issue_type="packspec_mismatch")
        self.assertEqual(issue.ref_code, "PS-1")
        # Drugi klik nie duplikuje.
        self.client.post(reverse("ui:packspec_report_mismatch", args=[row["spec"].pk]))
        self.assertEqual(PackagingIssue.objects.count(), 1)

    def test_new_batch_deactivates_previous(self):
        self._upload([["REF"], ["PS-1"]])
        self._upload([["REF"], ["PS-1"]])
        self.assertEqual(PackSpecBatch.objects.filter(is_active=True).count(), 1)
