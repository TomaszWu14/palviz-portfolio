"""HU label printing (module "Wydruk HU"): number reservation, ZPL output, role gating."""
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.labels import zpl_label
from ui.models import HUPrintProject, HUPrintRun
from ui.roles import GROUP_WAREHOUSE, GROUP_CONTROLLER


def _user(*groups):
    u = get_user_model().objects.create_user(username="u" + (groups[0] if groups else "x"), password="x")
    for g in groups:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class ZplLabelTests(TestCase):
    def test_code128_label_has_number_barcode_and_name(self):
        z = zpl_label("200004001", "Projekt Beta", "code128")
        self.assertTrue(z.startswith("^XA"))
        self.assertTrue(z.rstrip().endswith("^XZ"))
        self.assertIn("^BC", z)                 # Code128 command
        self.assertIn("200004001", z)           # the number
        self.assertIn("Projekt Beta", z)             # project name

    def test_code39_switch(self):
        z = zpl_label("100749998", "", "code39")
        self.assertIn("^B3", z)                 # Code39 command
        self.assertNotIn("^BC", z)

    def test_blank_name_prints_only_number(self):
        z = zpl_label("100749998", "", "code128")
        self.assertIn("100749998", z)

    def test_qr_both_corners_single_centred_number(self):
        """QR in both top corners (readable from either side), and a single centred number
        (one big number field). Interpretation line stays off."""
        z = zpl_label("100749998", "", "code128")
        self.assertEqual(z.count("^BQ"), 2)               # a QR in each top corner
        self.assertEqual(z.count("^FDHA,100749998"), 2)   # both QRs carry the number
        self.assertEqual(z.count("^A0N,159,159"), 1)      # exactly one big number
        self.assertIn(",N,N,N^FD100749998", z)            # code128 interpretation line off

    def test_named_projects_white_on_black_band(self):
        """Named projects (Projekt Beta / Projekt Gamma / Delta) print name + number white on a black
        band (^GB filled box + ^FR reversed text); POLSKA/EXPORT does not."""
        named = zpl_label("200004001", "Projekt Beta", "code128")
        self.assertIn("^GB", named)                       # filled black band
        self.assertEqual(named.count("^FR"), 2)           # name + number reversed white
        plain = zpl_label("100749998", "", "code128")
        self.assertNotIn("^GB", plain)
        self.assertNotIn("^FR", plain)


class HUPrintProjectTests(TestCase):
    def test_format_number_zero_pads(self):
        p = HUPrintProject(name="X", digits=9)
        self.assertEqual(p.format_number(4001), "000004001")
        self.assertEqual(p.format_number(200004001), "200004001")


class HUPrintViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.wh = _user(GROUP_WAREHOUSE)
        cls.other = _user(GROUP_CONTROLLER)
        cls.project = HUPrintProject.objects.create(
            name="Projekt Beta", label_text="Projekt Beta", next_number=200004001, digits=9)

    def test_home_requires_warehouse_role(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("ui:hu_print_home")).status_code, 403)
        self.client.force_login(self.wh)
        self.assertEqual(self.client.get(reverse("ui:hu_print_home")).status_code, 200)

    def test_generate_reserves_range_and_streams_zpl(self):
        self.client.force_login(self.wh)
        r = self.client.post(reverse("ui:hu_print_generate"),
                             {"project": self.project.pk, "quantity": 3, "symbology": "code128"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        body = b"".join(r.streaming_content).decode()
        # three labels for the three consecutive numbers
        self.assertEqual(body.count("^XA"), 3)
        for n in ("200004001", "200004002", "200004003"):
            self.assertIn(n, body)
        # counter advanced past the reserved range + audit row written (with username)
        self.project.refresh_from_db()
        self.assertEqual(self.project.next_number, 200004004)
        run = HUPrintRun.objects.get(project=self.project)
        self.assertEqual((run.from_number, run.to_number, run.quantity), (200004001, 200004003, 3))
        self.assertEqual(run.username, self.wh.username)

    def test_consecutive_runs_never_duplicate(self):
        self.client.force_login(self.wh)
        seen = set()
        for _ in range(3):
            r = self.client.post(reverse("ui:hu_print_generate"),
                                 {"project": self.project.pk, "quantity": 5})
            body = b"".join(r.streaming_content).decode()
            nums = re.findall(r"2000\d{5}", body)
            self.assertTrue(seen.isdisjoint(nums), "a number was reprinted across runs")
            seen.update(nums)
        self.assertEqual(len(seen), 15)

    def test_counter_edited_backwards_still_no_duplicate(self):
        """Even if the counter is set below already-printed numbers, the print view clamps
        the start above the highest printed number — no HU is ever reissued."""
        self.client.force_login(self.wh)
        first = self.client.post(reverse("ui:hu_print_generate"),
                                 {"project": self.project.pk, "quantity": 4})
        printed = set(re.findall(r"2000\d{5}", b"".join(first.streaming_content).decode()))
        # someone resets the counter backwards in the admin
        HUPrintProject.objects.filter(pk=self.project.pk).update(next_number=200004001)
        second = self.client.post(reverse("ui:hu_print_generate"),
                                  {"project": self.project.pk, "quantity": 4})
        reprinted = set(re.findall(r"2000\d{5}", b"".join(second.streaming_content).decode()))
        self.assertTrue(printed.isdisjoint(reprinted))     # no overlap despite the reset

    def test_preview_shows_next_number(self):
        self.client.force_login(self.wh)
        r = self.client.get(reverse("ui:hu_print_preview"), {"project": self.project.pk})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["number"], "200004001")
        self.assertContains(r, "200004001")
        self.assertContains(r, "type=qr")            # corner QR fallback images

    def test_log_shows_per_user_totals(self):
        self.client.force_login(self.wh)
        self.client.post(reverse("ui:hu_print_generate"), {"project": self.project.pk, "quantity": 6})
        r = self.client.get(reverse("ui:hu_print_log"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total_printed"], 6)
        rows = {u["username"]: u["labels"] for u in r.context["per_user"]}
        self.assertEqual(rows.get(self.wh.username), 6)

    def test_zero_quantity_rejected(self):
        self.client.force_login(self.wh)
        r = self.client.post(reverse("ui:hu_print_generate"),
                             {"project": self.project.pk, "quantity": 0})
        self.assertRedirects(r, reverse("ui:hu_print_home"))
        self.project.refresh_from_db()
        self.assertEqual(self.project.next_number, 200004001)   # unchanged

    def test_over_cap_rejected(self):
        self.client.force_login(self.wh)
        r = self.client.post(reverse("ui:hu_print_generate"),
                             {"project": self.project.pk, "quantity": 10_000_000})
        self.assertRedirects(r, reverse("ui:hu_print_home"))
        self.project.refresh_from_db()
        self.assertEqual(self.project.next_number, 200004001)   # unchanged
