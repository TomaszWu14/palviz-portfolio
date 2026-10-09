"""In-app HU control transaction: scan → count positions → finalize."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import Product, Shipment, ShipmentLine, PalletizationInstruction, HandlingUnit
from ui.roles import ALL_GROUPS
from huctl.views.hu import _generate_handling_units


def _user_all_roles(username="ctrl"):
    u = get_user_model().objects.create_user(username=username, password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class HUControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.sh = Shipment.objects.create(name="Dostawa 1", stowage_efficiency_pct=80,
                                          recipient_name="PHARMO DEMO SRL")
        p = Product.objects.create(code="NL100-100", name="NONVI lux")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=10, demand_pcs=100, is_active=True)
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=8, unit="kar", source_unit="OP")
        _generate_handling_units(cls.sh)
        cls.hu = cls.sh.handling_units.first()

    def setUp(self):
        self.client.force_login(self.user)

    def test_generation_fills_base_and_alt_units(self):
        it = self.hu.items.first()
        self.assertEqual(it.alt_unit, "KAR")
        self.assertEqual(it.base_unit, "OP")
        self.assertEqual(it.base_qty, it.alt_qty * 10)        # pcs_per_carton

    def test_open_detail_read_only_then_explicit_start(self):
        # GET detail jest read-only (P5) — nie flipuje statusu. Start dopiero na jawny POST.
        resp = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertEqual(resp.status_code, 200)
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "planned")          # podgląd nie startuje kontroli
        self.client.post(reverse("ui:hu_control_start", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "in_control")
        self.assertTrue(self.hu.is_blocked)

    def test_finalize_blocked_until_all_positions_counted(self):
        # B1: liczenie KAŻDEJ pozycji obowiązkowe — niepoliczone blokują księgowanie
        # (koniec auto-OK / confirm_untouched).
        self.client.post(reverse("ui:hu_control_start", args=[self.hu.pk]))
        # Bez policzenia — finalize odbija, HU zostaje w kontroli.
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "in_control")
        self.assertTrue(self.hu.items.filter(controlled=False).exists())
        # Policz każdą pozycję zgodnie z oczekiwaniem → finalize → ok.
        for it in self.hu.items.all():
            self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                             {"action": "confirm", "qty_base": str(it.base_qty)})
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "ok")
        self.assertFalse(self.hu.items.filter(controlled=False).exists())
        self.assertEqual(self.hu.items.filter(result="ok").count(), self.hu.items.count())

    def test_finalize_blocked_on_escaped_hu(self):
        # 'escaped' (wyjechało bez kontroli — decyzja lidera) jest ZAMKNIĘTY: finalize kontrolera
        # nie może go „odksięgować" do ok (wcześniej gate blokował tylko status=ok).
        self.hu.status = "escaped"
        self.hu.save(update_fields=["status"])
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]), {"confirm_untouched": "1"})
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "escaped")   # nie odksięgowany do ok

    def test_all_pallets_ok_fires_ready_notification_once(self):
        from ui.models import Notification
        planner = get_user_model().objects.create_user(username="tr", password="x", email="tr@z.pl")
        planner.groups.add(Group.objects.get_or_create(name="Transport")[0])
        self.sh.author_email = "tr@z.pl"
        self.sh.save(update_fields=["author_email"])
        for hu in list(self.sh.handling_units.all()):           # control + finalize every pallet
            self.client.post(reverse("ui:hu_control_start", args=[hu.pk]))
            for it in hu.items.all():                            # policz każdą pozycję (B1)
                self.client.post(reverse("ui:hu_control_count", args=[hu.pk, it.pk]),
                                 {"action": "confirm", "qty_base": str(it.base_qty)})
            with self.captureOnCommitCallbacks(execute=True):   # notify idzie po commicie
                self.client.post(reverse("ui:hu_control_finalize", args=[hu.pk]))
        self.sh.refresh_from_db()
        self.assertIsNotNone(self.sh.ready_notified_at)         # fired
        self.assertTrue(Notification.objects.filter(recipient=planner, title__icontains="gotowe").exists())
        first_at = self.sh.ready_notified_at
        hu = self.sh.handling_units.first()                     # re-finalize must not re-notify
        self.client.post(reverse("ui:hu_control_finalize", args=[hu.pk]))
        self.sh.refresh_from_db()
        self.assertEqual(self.sh.ready_notified_at, first_at)

    def test_discrepancy_marks_priority_and_notifies(self):
        from ui.models import Notification
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))   # start
        it = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "sure": "1", "qty_base": "0"})  # 0 vs expected → mismatch
        with self.captureOnCommitCallbacks(execute=True):       # notify idzie po commicie
            self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "to_recheck")
        self.assertTrue(self.hu.is_priority)
        self.assertTrue(Notification.objects.filter(title__icontains="rekontroli").exists())

    def test_bad_placement_requires_photo(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from ui.models import HUQualityIssue
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))   # start
        it = self.hu.items.first()
        # No photo → rejected (position stays uncounted).
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty), "flag_bad_placement": "on"})
        it.refresh_from_db()
        self.assertFalse(it.controlled)
        # With photo → accepted and the quality issue carries the photo.
        img = SimpleUploadedFile("p.jpg", b"\xff\xd8\xff\xe0jpg", content_type="image/jpeg")
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty),
                          "flag_bad_placement": "on", "photo": img})
        it.refresh_from_db()
        self.assertTrue(it.controlled)
        iss = HUQualityIssue.objects.filter(hu=self.hu, item=it, issue_type="bad_placement").first()
        self.assertIsNotNone(iss)
        self.assertTrue(bool(iss.photo))

    def test_alt_conversion_kar_then_opz(self):
        from huctl.views.hu_control import _alt_conv
        from ui.models import Product, PalletizationInstruction, HandlingUnitItem
        # KAR via pcs_per_carton (the position's product has 10 pcs/carton).
        self.assertEqual(_alt_conv(self.hu.items.first()), ("KAR", 10.0))
        # No carton conversion → fall back to OPZ via pcs_per_inner_pack.
        p2 = Product.objects.create(code="OPZ-1", name="Inner only")
        PalletizationInstruction.objects.create(
            product=p2, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=1, pcs_per_inner_pack=6, is_active=True)
        it = HandlingUnitItem.objects.create(hu=self.hu, product=p2, ref_code="OPZ-1",
                                             base_qty=12, alt_qty=12)
        self.assertEqual(_alt_conv(it), ("OPZ", 6.0))

    def test_unit_factors_four_tiles(self):
        from huctl.views.hu_control import _unit_factors
        from ui.models import Product, PalletizationInstruction, HandlingUnitItem
        # KAR defined, OPZ not → KAR dlt, OPZ greyed. No stored layout, but PAL is still
        # derived geometrically (8/layer × 8 layers = 64 cartons × 10 pcs = 640).
        f = _unit_factors(self.hu.items.first())
        self.assertEqual(f["kar"], 10.0)
        self.assertIsNone(f["opz"])
        self.assertEqual(f["pal"], 640.0)
        # PAL = a full pallet = cartons_per_pallet × pcs_per_carton (so 1 KAR ≠ 1 PAL).
        p2 = Product.objects.create(code="BOTH-1", name="Carton + inner")
        PalletizationInstruction.objects.create(
            product=p2, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=24, pcs_per_inner_pack=6, is_active=True,
            layouts=[{"name": "L1", "cartons_per_pallet": 30}], selected_layout="L1")
        it = HandlingUnitItem.objects.create(hu=self.hu, product=p2, ref_code="BOTH-1",
                                             base_qty=48, alt_qty=2)
        f2 = _unit_factors(it)
        self.assertEqual(f2["kar"], 24.0)
        self.assertEqual(f2["opz"], 6.0)
        self.assertEqual(f2["pal"], 30 * 24.0)       # 30 cartons/pallet × 24 pcs/carton
        self.assertNotEqual(f2["pal"], f2["kar"])    # 1 PAL ≠ 1 KAR

    def test_detail_renders_converter_tiles(self):
        # The detail screen shows the KAR / PAL tiles for an index with those conversions.
        # Kafle bez przelicznika (tu OPZ) już się NIE renderują (znikły szare „brak
        # przelicznika"). The clear button and the collapsible error bar are present;
        # the old leaked English comment is gone.
        resp = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        body = resp.content.decode()
        self.assertIn("KAR", body)
        self.assertIn("PAL", body)
        self.assertIn("Wyczyść", body)                # reset-the-tiles button
        self.assertIn("Zgłoś błędy", body)            # collapsible verification bar
        self.assertNotIn("Four counting tiles", body)  # broken multi-line {# #} no longer leaks
        self.assertIn("Brak specyficznych wymagań klienta", body)  # header fallback (no customer)

    def test_expiry_alert_levels(self):
        # Assert on the *applied* class (the CSS rule names also contain "exp-alert--").
        from datetime import timedelta
        today = timezone.localdate()
        it = self.hu.items.first()
        # < 6 months → red pulsing alert.
        it.expiry = today + timedelta(days=90); it.save(update_fields=["expiry"])
        body = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk])).content.decode()
        self.assertIn("exp-alert exp-alert--red", body)
        # 6–12 months → orange.
        it.expiry = today + timedelta(days=300); it.save(update_fields=["expiry"])
        body = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk])).content.decode()
        self.assertIn("exp-alert exp-alert--orange", body)
        self.assertNotIn("exp-alert exp-alert--red", body)
        # > 12 months → no alert applied.
        it.expiry = today + timedelta(days=500); it.save(update_fields=["expiry"])
        body = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk])).content.decode()
        self.assertNotIn("exp-alert exp-alert--", body)

    def test_open_quality_issue_blocks_finalize(self):
        from ui.models import HUQualityIssue
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))   # start
        it = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty)})
        HUQualityIssue.objects.create(hu=self.hu, item=it, issue_type="damaged", status="open")
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertNotEqual(self.hu.status, "ok")                 # blocked by open quality issue

    def test_expired_lot_blocks_finalize(self):
        from datetime import timedelta
        from ui.models import HUQualityIssue
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        it = self.hu.items.first()
        it.expiry = timezone.localdate() - timedelta(days=1)
        it.save(update_fields=["expiry"])
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty)})
        with self.captureOnCommitCallbacks(execute=True):       # notify idzie po commicie
            self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "to_recheck")            # blocked by expiry
        self.assertTrue(HUQualityIssue.objects.filter(hu=self.hu, issue_type="wrong_expiry", status="open").exists())

    def test_recheck_requires_different_controller(self):
        from django.contrib.auth.models import Group
        from ui.roles import GROUP_CONTROLLER
        U = get_user_model()
        grp = Group.objects.get_or_create(name=GROUP_CONTROLLER)[0]
        c1 = U.objects.create_user("c1", password="x"); c1.groups.add(grp)
        c2 = U.objects.create_user("c2", password="x"); c2.groups.add(grp)
        it = self.hu.items.first()
        # c1 wykonał PIERWOTNĄ kontrolę (audyt HUControlAttempt) — guard czyta pierwszego
        # kontrolera z audytu, odporny na późniejsze mutacje controlled_by (takeover/reopen).
        from ui.models import HUControlAttempt
        HUControlAttempt.objects.create(hu=self.hu, item=it, controller=c1,
                                        is_recheck=False, result="error")
        HandlingUnit.objects.filter(pk=self.hu.pk).update(status="to_recheck", controlled_by=c2)
        self.client.force_login(c1)                      # original controller → blocked
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty)})
        it.refresh_from_db(); self.assertFalse(it.controlled)
        self.client.force_login(c2)                      # different controller → allowed
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty)})
        it.refresh_from_db(); self.assertTrue(it.controlled)

    def test_count_match_then_finalize_ok(self):
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))   # start
        for it in self.hu.items.all():
            self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                             {"action": "confirm", "qty_base": str(it.base_qty)})
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "ok")

    def test_mismatch_sets_to_recheck_and_blocks(self):
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        it = self.hu.items.first()
        # Count one short → discrepancy even though operator pressed "confirm".
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "sure": "1", "qty_base": str(it.base_qty - 1)})
        it.refresh_from_db()
        self.assertEqual(it.result, "error")
        with self.captureOnCommitCallbacks(execute=True):       # notify idzie po commicie
            self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "to_recheck")
        self.assertTrue(self.hu.is_blocked)

    def test_mismatch_asks_confirmation_before_booking(self):
        # Entering a wrong qty does NOT book immediately — the controller is asked to
        # recount; only "Tak, jestem pewien" (sure=1) books it as a picker error.
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        it = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty - 1)})
        it.refresh_from_db()
        self.assertFalse(it.controlled)                  # not booked yet — awaiting confirm
        body = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk])).content.decode()
        self.assertIn("Tak, jestem pewien", body)        # confirm prompt shown
        # Confirm → booked as error.
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "sure": "1", "qty_base": str(it.base_qty - 1)})
        it.refresh_from_db()
        self.assertTrue(it.controlled)
        self.assertEqual(it.result, "error")

    def test_matching_qty_books_without_confirmation(self):
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        it = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty)})
        it.refresh_from_db()
        self.assertTrue(it.controlled)                   # exact qty → booked straight away
        self.assertEqual(it.result, "ok")

    def test_quality_flag_raises_issue_not_quantity_error(self):
        from ui.models import HUQualityIssue
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        it = self.hu.items.first()
        # Use a quality flag that doesn't require a photo (wrong_batch); damage/bad-placement
        # now need a photo, which is covered separately in test_quality_rules.
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty), "flag_wrong_batch": "on"})
        it.refresh_from_db()
        self.assertEqual(it.result, "ok")                  # quantity fine → not a quantity error
        self.assertTrue(it.error_flags.get("wrong_batch"))
        self.assertTrue(HUQualityIssue.objects.filter(hu=self.hu, issue_type="wrong_batch", status="open").exists())

    def test_additive_units_sum_to_expected(self):
        # Addytywnie (ppc=10): 1 KAR + reszta OP sumują się do oczekiwanej → OK.
        # (Konwerter lustrzany usunięty — kafle to niezależne składniki, bez auto-flagi AJM.)
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        it = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm",
                          "qty_base": str(it.base_qty - 10),   # reszta w OP
                          "c_kar": "1"})                       # + 1 karton = 10 OP
        it.refresh_from_db()
        self.assertEqual(it.result, "ok")
        self.assertFalse(it.error_flags.get("ajm_conversion"))   # brak auto cross-check

    def test_edit_reopens_position(self):
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        it = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty)})
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]), {"action": "edit"})
        it.refresh_from_db()
        self.assertFalse(it.controlled)

    def test_scanner_has_error_feedback_script(self):
        r = self.client.get(reverse("ui:hu_control_menu"))
        self.assertContains(r, "palvizBeep")          # audio+haptic feedback wired in

    def test_hu_history_lists_attempts(self):
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        it = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty)})
        r = self.client.get(reverse("ui:hu_control_history", args=[self.hu.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Historia kontroli")
        self.assertContains(r, it.ref_code)

    def test_error_report_paginates_rows(self):
        # 51 pozycji z błędem → raport paginuje po 50, agregat total = 51 (pełny zbiór).
        from ui.models import HandlingUnitItem
        for i in range(51):
            HandlingUnitItem.objects.create(hu=self.hu, ref_code=f"E{i:02d}", base_qty=1,
                                            base_unit="OP", result="error")
        r1 = self.client.get(reverse("ui:hu_error_report"))
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.context["total"], 51)             # agregat nad pełnym zbiorem
        self.assertEqual(r1.context["page_obj"].paginator.count, 51)
        self.assertEqual(len(r1.context["page_obj"].object_list), 50)
        r2 = self.client.get(reverse("ui:hu_error_report"), {"page": 2})
        self.assertEqual(len(r2.context["page_obj"].object_list), 1)

    def test_find_recipient_paginates_groups(self):
        # 26 różnych odbiorców pasujących do zapytania → 2 strony po 25 grup.
        for i in range(26):
            sh = Shipment.objects.create(name=f"S{i}", recipient_name=f"PAGEC {i:02d}")
            HandlingUnit.objects.create(shipment=sh, seq=1, code=f"PG{i:02d}")
        r1 = self.client.get(reverse("ui:hu_control_find_recipient"), {"q": "PAGEC"})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.context["page_obj"].paginator.count, 26)
        self.assertEqual(r1.context["page_obj"].paginator.num_pages, 2)
        self.assertEqual(len(r1.context["groups"]), 25)          # strona 1
        r2 = self.client.get(reverse("ui:hu_control_find_recipient"), {"q": "PAGEC", "page": 2})
        self.assertEqual(len(r2.context["groups"]), 1)           # reszta na stronie 2

    def test_hu_history_uses_polish_labels(self):
        """UX #10: kolumna Wynik i historia statusu pokazują etykiety PL, nie surowe
        kody (ok/error, from→to_status)."""
        from ui.models import HUControlAttempt, HUStatusEvent
        it = self.hu.items.first()
        HUControlAttempt.objects.create(hu=self.hu, item=it, controller=self.user, result="error")
        HUStatusEvent.objects.create(hu=self.hu, from_status="in_control", to_status="ok")
        r = self.client.get(reverse("ui:hu_control_history", args=[self.hu.pk]))
        self.assertContains(r, "Błąd")
        self.assertNotContains(r, ">error<")          # surowy kod nie może trafić do komórki
        # status: from/to_status renderowane przez get_*_display (PL), nie surowo
        self.assertNotContains(r, ">in_control →")

    def test_scan_resolves_hu(self):
        resp = self.client.post(reverse("ui:hu_control_scan"), {"code": self.hu.ref})
        self.assertRedirects(resp, reverse("ui:hu_control_detail", args=[self.hu.pk]))

    def test_find_recipient(self):
        resp = self.client.get(reverse("ui:hu_control_find_recipient"), {"q": "PHARMO"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.hu.ref)

    def test_warehouse_type_filter(self):
        # Put the HU into to_recheck and tag a warehouse type, then filter by it.
        HandlingUnit.objects.filter(pk=self.hu.pk).update(status="to_recheck", warehouse_type="WCGL")
        rl = self.client.get(reverse("ui:hu_control_recheck_list"), {"wh": "WCGL"})
        self.assertContains(rl, self.hu.ref)
        # a different type filters it out
        other = self.client.get(reverse("ui:hu_control_recheck_list"), {"wh": "WT99"})
        self.assertNotContains(other, self.hu.ref)
        # find-recipient can filter on warehouse type alone
        fr = self.client.get(reverse("ui:hu_control_find_recipient"), {"wh": "WCGL"})
        self.assertContains(fr, self.hu.ref)

    def test_recheck_list_and_resolution(self):
        # Force the HU into to_recheck with a QUANTITY discrepancy.
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        it = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "sure": "1", "qty_base": str(it.base_qty - 1)})
        with self.captureOnCommitCallbacks(execute=True):       # notify idzie po commicie
            self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "to_recheck")

        # Re-control list shows it; correct the position → finalize → ok.
        rl = self.client.get(reverse("ui:hu_control_recheck_list"))
        self.assertContains(rl, self.hu.ref)
        # Druga para oczu: rekontrolę wykonuje INNY kontroler (autor pierwotnej kontroli
        # jest blokowany niezależnie od roli — patrz test_hu_recheck_second_pair).
        second = _user_all_roles("drugi-kontroler")
        self.client.force_login(second)
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "qty_base": str(it.base_qty)})
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.status, "ok")

    def test_status_by_warehouse_type(self):
        HandlingUnit.objects.filter(pk=self.hu.pk).update(warehouse_type="WT01")
        resp = self.client.get(reverse("ui:hu_control_status"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "WT01")

    def test_error_report_lists_quantity_errors(self):
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        it = self.hu.items.first()
        self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                         {"action": "confirm", "sure": "1", "qty_base": str(it.base_qty - 1)})   # quantity error
        resp = self.client.get(reverse("ui:hu_error_report"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, it.ref_code)
        # CSV export.
        csv = self.client.get(reverse("ui:hu_error_report"), {"export": "csv"})
        self.assertEqual(csv["Content-Type"].split(";")[0], "text/csv")
        self.assertIn(it.ref_code, csv.content.decode("utf-8-sig"))

    def test_workflow_and_ready_signal(self):
        # All HUs controlled OK → hu_checked_ready True and the HU milestone done.
        self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        for it in self.hu.items.all():
            self.client.post(reverse("ui:hu_control_count", args=[self.hu.pk, it.pk]),
                             {"action": "confirm", "qty_base": str(it.base_qty)})
        self.client.post(reverse("ui:hu_control_finalize", args=[self.hu.pk]))
        self.sh.refresh_from_db()
        self.assertTrue(self.sh.hu_checked_ready())
        hu_step = next(s for s in self.sh.workflow() if s["key"] == "hu")
        self.assertEqual(hu_step["state"], "done")

    def test_control_only_user_lands_in_scanner(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from ui.roles import GROUP_CONTROLLER, GROUP_TRANSPORT
        # Control-only operator → home redirects into the control module.
        op = get_user_model().objects.create_user(username="op", password="x")
        op.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        self.client.force_login(op)
        resp = self.client.get(reverse("ui:warehouse_search"))
        # Spec UX 2026-09-03 §1: kontroler control-only ląduje na pulpicie „Moja zmiana".
        self.assertRedirects(resp, reverse("ui:hu_my_shift"))
        # A transport user is NOT redirected (full app access).
        tr = get_user_model().objects.create_user(username="tr", password="x")
        tr.groups.add(Group.objects.get_or_create(name=GROUP_TRANSPORT)[0])
        self.client.force_login(tr)
        self.assertEqual(self.client.get(reverse("ui:warehouse_search")).status_code, 200)

    def test_controller_role_required(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from ui.roles import GROUP_CONTROLLER
        # A user without any control role is denied.
        other = get_user_model().objects.create_user(username="nobody", password="x")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("ui:hu_control_menu")).status_code, 403)
        # With the controller group → allowed.
        other.groups.add(Group.objects.get_or_create(name=GROUP_CONTROLLER)[0])
        self.assertEqual(self.client.get(reverse("ui:hu_control_menu")).status_code, 200)
