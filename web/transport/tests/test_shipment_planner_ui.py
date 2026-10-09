"""Planner shipment list/import UI: transport-type capture at import, quote-currency
toggle, the anchored-pallet-count slider lock, and the upcoming-only pickup schedule."""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ui.models import (
    Shipment, ShipmentLine, ShipmentQuoteOffer, WarehouseReadiness,
    Product, PalletizationInstruction,
)
from ui.roles import ALL_GROUPS


def _user_all_roles():
    u = get_user_model().objects.create_user(username="planner", password="x")
    for g in ALL_GROUPS:
        u.groups.add(Group.objects.get_or_create(name=g)[0])
    return u


class ShipmentImportModesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        Product.objects.create(code="MOD-1", name="Mod 1")

    def setUp(self):
        self.client.force_login(self.user)

    def test_import_captures_selected_transport_modes(self):
        csv = b"Dokument,Produkt,Ilosc,Jednostka miary\n900001,MOD-1,10,KAR\n"
        f = SimpleUploadedFile("dostawa.csv", csv, content_type="text/csv")
        resp = self.client.post(reverse("ui:planner_shipments_import"),
                                {"file": f, "modes": ["naczepa", "cont40"]})
        self.assertEqual(resp.status_code, 302)
        sh = Shipment.objects.get(name__icontains="900001")
        self.assertEqual(set(sh.transport_mode_keys()), {"naczepa", "cont40"})

    def test_import_captures_load_mode(self):
        csv = b"Dokument,Produkt,Ilosc,Jednostka miary\n900003,MOD-1,10,KAR\n"
        f = SimpleUploadedFile("d3.csv", csv, content_type="text/csv")
        self.client.post(reverse("ui:planner_shipments_import"),
                         {"file": f, "modes": ["cont40"], "load_mode": "loose"})
        sh = Shipment.objects.get(name__icontains="900003")
        self.assertEqual(sh.load_mode, "loose")
        self.assertTrue(sh.wants_container_viz())

    def test_import_ignores_unknown_modes(self):
        csv = b"Dokument,Produkt,Ilosc,Jednostka miary\n900002,MOD-1,10,KAR\n"
        f = SimpleUploadedFile("d2.csv", csv, content_type="text/csv")
        self.client.post(reverse("ui:planner_shipments_import"),
                         {"file": f, "modes": ["naczepa", "rakieta"]})
        sh = Shipment.objects.get(name__icontains="900002")
        self.assertEqual(sh.transport_mode_keys(), ["naczepa"])  # bogus key dropped

    def test_list_shows_transport_type_and_renders(self):
        Shipment.objects.create(name="Z modami", transport_modes="naczepa,cont40")
        resp = self.client.get(reverse("ui:planner_shipments"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Naczepa Standard")
        self.assertContains(resp, "Kontener 40")          # apostrophe gets HTML-escaped


class ShipmentListCurrencyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.sh = Shipment.objects.create(name="EUR oferta")
        ShipmentQuoteOffer.objects.create(
            shipment=cls.sh, carrier_name="DHL", amount=Decimal("100"), currency="EUR",
            submitted_at=timezone.now())

    def setUp(self):
        self.client.force_login(self.user)

    def test_both_currencies_computed_side_by_side(self):
        # 100 EUR offer → EUR column 100, PLN column 100×4 = 400, with the rate in the header.
        with patch("ui.nbp.get_rate", side_effect=lambda c: Decimal("4") if c == "EUR" else Decimal("1")):
            resp = self.client.get(reverse("ui:planner_shipments"))
        self.assertEqual(resp.status_code, 200)
        sc = [s for s in resp.context["page_obj"] if s.pk == self.sh.pk][0]
        self.assertEqual(round(sc.quote_eur), 100)
        self.assertEqual(round(sc.quote_pln), 400)
        self.assertEqual(resp.context["eur_rate"], Decimal("4"))
        self.assertContains(resp, "1 EUR =")               # rate shown in the header

    def test_selected_but_unpriced_offer_does_not_blank_value(self):
        # Regression: an offer marked selected before the forwarder enters a price must not
        # blank the row — fall back to the cheapest priced offer.
        ShipmentQuoteOffer.objects.create(
            shipment=self.sh, carrier_name="Nordfreight", amount=None, currency="EUR",
            selected=True, submitted_at=timezone.now())
        with patch("ui.nbp.get_rate", side_effect=lambda c: Decimal("4") if c == "EUR" else Decimal("1")):
            resp = self.client.get(reverse("ui:planner_shipments"))
        sc = [s for s in resp.context["page_obj"] if s.pk == self.sh.pk][0]
        self.assertEqual(round(sc.quote_eur), 100)          # the priced DHL offer, not None


class AnchoredSliderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        p = Product.objects.create(code="ANC-1", name="Anc")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=1, demand_pcs=100, is_active=True)
        cls.sh = Shipment.objects.create(name="Kotwica", stowage_efficiency_pct=85)
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=300, unit="kar")

    def setUp(self):
        self.client.force_login(self.user)

    def test_slider_locked_when_warehouse_pallets_set(self):
        self.sh.warehouse_pallets = 2
        self.sh.save(update_fields=["warehouse_pallets"])
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]))
        self.assertContains(resp, "suwak nieaktywny")

    def test_slider_active_when_not_anchored(self):
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]))
        self.assertNotContains(resp, "suwak nieaktywny")
        self.assertContains(resp, "bumpEff")

    def test_inline_anchor_note_saves_from_detail(self):
        self.sh.warehouse_pallets = 2
        self.sh.save(update_fields=["warehouse_pallets"])
        # The anchored banner offers an inline comment field…
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]))
        self.assertContains(resp, "save_anchor_note")
        # …and saving it persists without opening the edit form.
        self.client.post(reverse("ui:planner_shipment_detail", args=[self.sh.pk]),
                         {"save_anchor_note": "1", "anchor_note": "magazyn dał 2 mimo szacunku"})
        self.sh.refresh_from_db()
        self.assertEqual(self.sh.anchor_note, "magazyn dał 2 mimo szacunku")


class ShipmentDefinitionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        p = Product.objects.create(code="DEF-1", name="Def")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=0.5, pcs_per_carton=1, demand_pcs=100, is_active=True)
        cls.sh = Shipment.objects.create(name="Def dostawa", transport_modes="naczepa,cont40")
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=50, unit="kar")

    def setUp(self):
        self.client.force_login(self.user)

    def test_default_is_pallets_view(self):
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]))
        self.assertEqual(resp.context["viz_kind"], "pallets")
        self.assertContains(resp, "Definicja wysyłki")          # the definition bar renders

    def test_define_loose_switches_to_container_view(self):
        resp = self.client.post(reverse("ui:planner_shipment_detail", args=[self.sh.pk]),
                                {"define": "1", "vehicle": "cont40", "load_mode": "loose"})
        self.assertEqual(resp.status_code, 302)
        self.sh.refresh_from_db()
        self.assertEqual(self.sh.load_mode, "loose")
        self.assertEqual(self.sh.primary_vehicle_key(), "cont40")   # chosen type moved to front
        self.assertTrue(self.sh.wants_container_viz())
        det = self.client.get(reverse("ui:planner_shipment_detail", args=[self.sh.pk]))
        self.assertEqual(det.context["viz_kind"], "container")
        self.assertContains(det, "mode=loose")                   # embeds the container view

    def test_container_vehicle_forces_container_view_even_on_pallets(self):
        # A sea container chosen → container view regardless of load mode.
        self.client.post(reverse("ui:planner_shipment_detail", args=[self.sh.pk]),
                         {"define": "1", "vehicle": "cont20", "load_mode": "pallets"})
        self.sh.refresh_from_db()
        self.assertTrue(self.sh.wants_container_viz())


class ShipmentThreeWarningsTests(TestCase):
    """Panel 3D sygnalizuje karton cięższy niż limit palety — karton dalej jest rysowany."""
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        heavy = Product.objects.create(code="HEAVY-1", name="Ciężki")
        PalletizationInstruction.objects.create(
            product=heavy, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=1200, pcs_per_carton=1, demand_pcs=10, is_active=True)
        light = Product.objects.create(code="LIGHT-1", name="Lekki")
        PalletizationInstruction.objects.create(
            product=light, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=1, pcs_per_carton=1, demand_pcs=10, is_active=True)
        cls.heavy_sh = Shipment.objects.create(name="Ciężka")
        ShipmentLine.objects.create(shipment=cls.heavy_sh, product=heavy, quantity=2, unit="kar")
        cls.light_sh = Shipment.objects.create(name="Lekka")
        ShipmentLine.objects.create(shipment=cls.light_sh, product=light, quantity=20, unit="kar")

    def setUp(self):
        self.client.force_login(self.user)

    def test_overweight_carton_warning_in_3d_panel(self):
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[self.heavy_sh.pk]))
        self.assertContains(resp, "Model 3D — przekroczone limity palety")
        self.assertContains(resp, "Karton HEAVY-1 (1200 kg) cięższy niż limit palety")
        drawn = [len(p["boxes"]) for sc in resp.context["calc"]["scenarios"]
                 for p in sc["three"]["pallets"] if p["boxes"]]
        self.assertEqual(sum(drawn), 2 * len(resp.context["calc"]["scenarios"]))  # nic nie znika

    def test_no_warning_box_for_regular_load(self):
        resp = self.client.get(reverse("ui:planner_shipment_detail", args=[self.light_sh.pk]))
        self.assertNotContains(resp, "przekroczone limity palety")


class CmrDocumentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from ui.models import Customer
        cls.user = _user_all_roles()
        p = Product.objects.create(code="CMR-PROD", name="Rękawice")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=1, pcs_per_carton=1, demand_pcs=100, is_active=True)
        cust = Customer.objects.create(name="Nordmed ehf.", code="C1", city="Reykjavik",
                                       country="IS", street="Hverfisgata 1")
        cls.sh = Shipment.objects.create(name="Dostawa 81772168", customer=cust,
                                         destination_country="IS", destination_city="Reykjavik")
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=30, unit="kar")

    def setUp(self):
        self.client.force_login(self.user)

    def test_cmr_renders(self):
        resp = self.client.get(reverse("ui:planner_shipment_cmr", args=[self.sh.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.content)                       # PDF bytes or HTML fallback
        ct = resp["Content-Type"]
        self.assertTrue("pdf" in ct or "html" in ct)
        if "html" in ct:                                    # WeasyPrint native libs absent → HTML
            self.assertContains(resp, "LIST PRZEWOZOWY CMR")
            self.assertContains(resp, "CMR-PROD")
            self.assertContains(resp, "Nordmed")


class TransportKpiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        p = Product.objects.create(code="K1", name="K")
        PalletizationInstruction.objects.create(
            product=p, version=1, carton_l=40, carton_w=30, carton_h=25,
            unit_weight=1, pcs_per_carton=1, demand_pcs=100, is_active=True)
        cls.sh = Shipment.objects.create(name="KPI", destination_country="DE")
        ShipmentLine.objects.create(shipment=cls.sh, product=p, quantity=20, unit="kar")
        ShipmentQuoteOffer.objects.create(
            shipment=cls.sh, carrier_name="DHL", amount=Decimal("1000"), currency="PLN",
            selected=True, submitted_at=timezone.now())

    def setUp(self):
        self.client.force_login(self.user)

    def test_dashboard_renders_with_kpis(self):
        resp = self.client.get(reverse("ui:planner_transport_kpi"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "KPI transportu")
        kpi = resp.context["kpi"]
        self.assertEqual(kpi["n_quoted"], 1)
        self.assertGreater(kpi["avg_cost"], 0)              # 1000 PLN offer counted
        self.assertGreaterEqual(kpi["n_active"], 1)

    def test_dashboard_renders_from_snapshot_without_recompute(self):
        """Świeży snapshot → render z cache, zero przeliczania packingu w request."""
        from unittest.mock import patch
        from ui.models import TransportKpiSnapshot
        self.client.get(reverse("ui:planner_transport_kpi"))     # pierwszy hit tworzy snapshot
        self.assertIsNotNone(TransportKpiSnapshot.load())
        with patch("transport.kpi.compute_transport_kpi") as mocked:
            resp = self.client.get(reverse("ui:planner_transport_kpi"))
        self.assertEqual(resp.status_code, 200)
        mocked.assert_not_called()                               # świeży cache → bez przeliczeń
        self.assertEqual(resp.context["kpi"]["n_quoted"], 1)

    def test_stale_snapshot_recomputed(self):
        from ui.models import TransportKpiSnapshot
        from transport.views.shipments import refresh_transport_kpi_snapshot
        snap = refresh_transport_kpi_snapshot()
        # Postarz snapshot poza TTL (update bez auto_now przez queryset.update).
        TransportKpiSnapshot.objects.filter(pk=snap.pk).update(
            updated_at=timezone.now() - datetime.timedelta(hours=2))
        resp = self.client.get(reverse("ui:planner_transport_kpi"))
        self.assertEqual(resp.status_code, 200)
        snap.refresh_from_db()
        self.assertLess((timezone.now() - snap.updated_at).total_seconds(), 60)


class PickupScheduleUpcomingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        cls.past = Shipment.objects.create(name="Stara dostawa")
        cls.future = Shipment.objects.create(name="Przyszla dostawa")

    def setUp(self):
        self.client.force_login(self.user)

    def test_past_hidden_by_default_shown_with_all(self):
        from datetime import timedelta
        today = timezone.localdate()
        WarehouseReadiness.objects.create(shipment=self.past, kind="date", status="yes",
                                          pickup_date=today - timedelta(days=5))
        WarehouseReadiness.objects.create(shipment=self.future, kind="date", status="yes",
                                          pickup_date=today + timedelta(days=5))
        resp = self.client.get(reverse("ui:planner_pickup_schedule"))
        self.assertContains(resp, "Przyszla dostawa")
        self.assertNotContains(resp, "Stara dostawa")          # past hidden by default
        resp_all = self.client.get(reverse("ui:planner_pickup_schedule"), {"all": "1"})
        self.assertContains(resp_all, "Stara dostawa")         # revealed with ?all=1


class ShipmentListPaginationTests(TestCase):
    """The transport list used to load ALL shipments and run per-row KPI queries over
    each — unbounded work on the busiest page. It's now paginated (25/page) and the
    per-row calc is one queryset for lines, not two. Guard both against regression."""
    @classmethod
    def setUpTestData(cls):
        cls.user = _user_all_roles()
        prod = Product.objects.create(code="PAG-1", name="Pag 1")
        # 30 shipments (> one page of 25), each with a line so the per-row KPI pass runs
        # its lines query. (No instruction needed — the query COUNT per row is what we
        # guard, and that's identical whether or not a line resolves to a pallet.)
        for i in range(30):
            sh = Shipment.objects.create(name=f"Dostawa {i:03d}", is_stock=False)
            ShipmentLine.objects.create(shipment=sh, product=prod, quantity=10, unit="kar")

    def setUp(self):
        self.client.force_login(self.user)

    def test_list_is_paginated(self):
        resp = self.client.get(reverse("ui:planner_shipments"))
        self.assertEqual(resp.status_code, 200)
        page_obj = resp.context["page_obj"]
        self.assertEqual(page_obj.paginator.count, 30)
        self.assertEqual(len(page_obj.object_list), 25)     # one page, not all 30
        self.assertTrue(page_obj.has_other_pages())

    def test_query_count_does_not_grow_with_more_shipments(self):
        # The number itself isn't the contract — its INVARIANCE to row count is. Measure
        # the ceiling at 30 rows, add a second full page, assert it's unchanged. Before
        # pagination this grew ~3 queries per extra shipment (the N+1).
        baseline = self._list_queries()
        for i in range(30, 60):
            sh = Shipment.objects.create(name=f"Extra {i:03d}", is_stock=False)
            ShipmentLine.objects.create(
                shipment=sh, product=Product.objects.get(code="PAG-1"),
                quantity=5, unit="kar")
        self.assertEqual(self._list_queries(), baseline)

    def _list_queries(self):
        from django.core.cache import cache
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        # PresenceMiddleware dokłada UPDATE last_seen, gdy klucz presence:<pk> (60 s) jest
        # pusty — cache żyje między testami, więc bez tego liczba skakała 118/119 zależnie
        # od tego, czy poprzedni test (i ile sekund temu) rozgrzał klucz.
        cache.set(f"presence:{self.user.pk}", 1, 60)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse("ui:planner_shipments"))
        return len(ctx.captured_queries)

    def test_query_ceiling(self):
        # PERF-002: linie + instrukcje batchowane dla całej strony, grupy/nadpisania modułów
        # pamiętane na użytkowniku w nawigacji — było ~118 zapytań na render, teraz < 20.
        self.assertLess(self._list_queries(), 20)
