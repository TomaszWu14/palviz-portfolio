"""Asystent produktu w MatInfo (PHV) — 100% Ollama. Endpoint karmi lokalny model
danymi z build_hierarchy; gdy Ollama nieskonfigurowane → łagodny 503, nie 500.
Realny model NIE jest wołany (zaria_llm zamockowany)."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import Product, PalletizationInstruction
from ui.roles import GROUP_WAREHOUSE


def _wh():
    u = get_user_model().objects.create_user(username="mag", password="x")
    u.groups.add(Group.objects.get_or_create(name=GROUP_WAREHOUSE)[0])
    return u


def _product(code="DMO-1"):
    p = Product.objects.create(code=code, name="Rękawice", ean="5900000000001",
                               unit_length_cm=21, unit_width_cm=12, unit_height_cm=5)
    PalletizationInstruction.objects.create(
        product=p, name="v1", is_active=True, version=1, unit_weight=0.4, pcs_per_carton=10,
        carton_l=29, carton_w=25, carton_h=22,
        pallet_length_cm=120, pallet_width_cm=80,
        pallet_base_height_cm=15, max_height_total_cm=213,
        layouts=[{"name": "L1", "cartons_per_pallet": 99, "layers_used": 9, "placements": []}])
    return p


class MatinfoAssistantTest(TestCase):
    def setUp(self):
        self.client.force_login(_wh())
        self.url = reverse("ui:phv_assistant")

    def test_503_when_ollama_not_configured(self):
        # domyślnie brak ZARIA_OLLAMA_BASE_URL → is_configured("ollama") == False
        _product()
        resp = self.client.post(self.url, {"ref": "DMO-1"})
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(resp.json()["ok"])

    @patch("ui.zaria_llm.complete", return_value=("Odpowiedź modelu.", 10, 20, 5))
    @patch("ui.zaria_llm.is_configured", return_value=True)
    def test_answers_and_feeds_system_context(self, _cfg, mock_complete):
        _product()
        resp = self.client.post(self.url, {"ref": "DMO-1", "question": "Ile na palecie?"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["answer"], "Odpowiedź modelu.")
        # provider wymuszony na ollama (bez clouda) + kontekst niesie dane produktu
        model_arg, messages_arg, system_prompt = mock_complete.call_args.args[:3]
        self.assertEqual(model_arg.provider, "ollama")
        self.assertIn("DMO-1", system_prompt)
        self.assertIn("Karton", system_prompt)          # poziom z build_hierarchy
        self.assertEqual(messages_arg[0]["content"], "Ile na palecie?")

    @patch("ui.zaria_llm.complete", return_value=("ok", 1, 1, 1))
    @patch("ui.zaria_llm.is_configured", return_value=True)
    def test_context_includes_warehouse_data(self, _cfg, mock_complete):
        from ui.models import Shipment, HandlingUnit, HandlingUnitItem
        p = _product()
        sh = Shipment.objects.create(name="Stock", is_stock=True)
        hu = HandlingUnit.objects.create(shipment=sh, seq=1, code="HU-Z1",
                                         warehouse_type="0010", location="C1-02-030B")
        HandlingUnitItem.objects.create(hu=hu, product=p, ref_code=p.code)
        self.client.post(self.url, {"ref": "DMO-1"})
        system_prompt = mock_complete.call_args.args[2]
        self.assertIn("Dane magazynowe", system_prompt)
        self.assertIn("0010", system_prompt)                # typ + liczba palet w kontekście

    @patch("ui.zaria_llm.complete", return_value=("ok", 1, 1, 1))
    @patch("ui.zaria_llm.is_configured", return_value=True)
    def test_context_lists_picking_process_locations(self, _cfg, mock_complete):
        # Regresja: lokalizacje procesu (0050/0052/0070) to słowniki {code, qty} — wcześniej
        # ", ".join(...) na słownikach rzucał TypeError → 500 dla REF ze stockiem w procesie.
        from ui.models import Shipment, HandlingUnit, HandlingUnitItem
        p = _product()
        for i, loc in enumerate(("N1-01", ""), start=1):
            sh = Shipment.objects.create(name=f"Stock {i}", is_stock=True)
            hu = HandlingUnit.objects.create(shipment=sh, seq=1, code=f"HU-P{i}",
                                             warehouse_type="0052", location=loc)
            HandlingUnitItem.objects.create(hu=hu, product=p, ref_code=p.code, expected_qty=12)
        resp = self.client.post(self.url, {"ref": "DMO-1"})
        self.assertEqual(resp.status_code, 200)
        system_prompt = mock_complete.call_args.args[2]
        self.assertIn("Lokalizacje / strategia magazynowa", system_prompt)
        self.assertIn("- 0052 Near to bin: N1-01 (12 szt), — (12 szt)", system_prompt)

    @patch("ui.zaria_llm.is_configured", return_value=True)
    def test_404_for_unknown_ref(self, _cfg):
        resp = self.client.post(self.url, {"ref": "NIE-MA"})
        self.assertEqual(resp.status_code, 404)

    def test_home_hides_assistant_when_not_configured(self):
        _product()
        resp = self.client.get(reverse("ui:phv_home"), {"q": "DMO-1"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["assistant_enabled"])
        self.assertNotContains(resp, "Asystent produktu")
