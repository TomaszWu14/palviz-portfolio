"""Testy charakteryzujące widoku carton_opt_variants (CODE-001) — przypinają OBECNE zachowanie
przed podziałem funkcji na prywatne helpery.

Pełny kontekst szablonu `ui/carton_opt/variants.html` dla zestawu scenariuszy (parametry GET
product/annual/suggest/issue, brak danych, sloty B/C hierarchii, promocje, zgłoszenia) jest
normalizowany (bez PK i dat: obiekty ORM → kody/etykiety, dane 3D → skrót SHA-256) i porównywany
ze snapshotem JSON (data/carton_opt_variants_snapshot.json). Regeneracja (tylko świadomie, gdy
zmiana zachowania jest zamierzona):
    CARTON_OPT_VARIANTS_SNAPSHOT_UPDATE=1 python manage.py test ui.tests.test_carton_opt_variants_characterization
"""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from ui.models import (CartonAlternative, CartonPromotion, InnerPack, PackagingIssue,
                       PalletizationInstruction, Product)
from ui.roles import GROUP_ADMIN, GROUP_MASTER_DATA, GROUP_OPTIMIZER, GROUP_VIEWER

SNAPSHOT = Path(__file__).with_name("data") / "carton_opt_variants_snapshot.json"
URL = "ui:carton_opt_variants"


def _user(name, group=None, **kw):
    u = get_user_model().objects.create_user(username=name, password="x", **kw)
    if group:
        u.groups.add(Group.objects.get_or_create(name=group)[0])
    return u


def _three(td):
    """three_data (JSON str) → stabilny skrót: typ + klucze + SHA-256 kanonicznego JSON-a."""
    if not td:
        return td
    data = json.loads(td)
    canon = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return {"type": data.get("type"), "keys": sorted(data),
            "n_placements": len(data.get("placements") or []),
            "sha256": hashlib.sha256(canon.encode()).hexdigest()}


def _levels(levels):
    if levels is None:
        return None
    return [{**{k: v for k, v in lvl.items() if k != "three_data"},
             "three_data": _three(lvl["three_data"])} for lvl in levels]


def _row(r):
    d = {k: v for k, v in r.items() if k not in ("alt", "history")}
    d["alt"] = {"label": r["alt"].label, "slot": r["alt"].slot}
    d["history"] = [{k: v for k, v in h.items() if k != "when"} for h in r["history"]]
    return d


def _normalize(ctx):
    """Kontekst widoku bez PK/dat/obiektów ORM → struktura JSON-owalna i stabilna."""
    cfg = ctx["cfg"]
    issue = ctx["issue"]
    out = {
        "q": ctx["q"],
        "product": ctx["product"].code if ctx["product"] else None,
        "instr": ({"product": ctx["instr"].product.code, "version": ctx["instr"].version}
                  if ctx["instr"] else None),
        "baseline": ctx["baseline"],
        "rows": [_row(r) for r in ctx["rows"]],
        "suggestions": ctx["suggestions"],
        "dim_suggestions": ctx["dim_suggestions"],
        "cfg": {"min_fill_pct": cfg.min_fill_pct, "down": cfg.suggest_vol_down_pct,
                "up": cfg.suggest_vol_up_pct, "ppt": cfg.pallets_per_truck},
        "annual": ctx["annual"],
        "promotions": [{"version": p.version, "dims": p.dims, "label": p.source_label,
                        "fill_before": p.fill_before, "fill_after": p.fill_after,
                        "user": p.user.username if p.user else None}
                       for p in ctx["promotions"]],
        "issue": ({"ref_code": issue.ref_code, "product": issue.product.code if issue.product else None}
                  if issue else None),
        "pallet_three": _three(ctx["pallet_three"]),
        "hier_a": _levels(ctx["hier_a"]),
        "variant_b": ctx["variant_b"].label if ctx["variant_b"] else None,
        "hier_b": _levels(ctx["hier_b"]),
        "variant_c": ctx["variant_c"].label if ctx["variant_c"] else None,
        "hier_c": _levels(ctx["hier_c"]),
    }
    return json.loads(json.dumps(out, sort_keys=True, default=str))


def _instr(product, **kw):
    base = dict(product=product, version=1, is_active=True, unit_weight=1.0, pcs_per_carton=10,
                carton_l=20, carton_w=20, carton_h=120, pallet_length_cm=120,
                pallet_width_cm=80, max_height_total_cm=215, pallet_base_height_cm=15,
                max_weight_kg=1000, demand_pcs=100000)
    base.update(kw)
    return PalletizationInstruction.objects.create(**base)


class CartonOptVariantsCharacterizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.op = _user("op", GROUP_OPTIMIZER)
        P = Product.objects.create
        # Pełny materiał: wymiary sztuki, OPZ w instrukcji, warianty z historią, sloty B/C.
        cls.full = P(code="CV-FULL", name="Pełny wariantowy", ean="5901234123457",
                     unit_length_cm=5, unit_width_cm=5, unit_height_cm=10)
        ip = InnerPack.objects.create(name="OPZ 20×15×12", length_cm=20.0, width_cm=15.0,
                                      height_cm=12.0)
        _instr(cls.full, inner_pack=ip)
        A = CartonAlternative.objects.create
        flat = A(product=cls.full, label="Płaski", length_cm=40, width_cm=40, height_cm=30)
        flat.length_cm = 45
        flat.save()                                   # historia: 2 wpisy
        A(product=cls.full, label="Gigant", length_cm=300, width_cm=300, height_cm=300)
        A(product=cls.full, label="Ukryty", length_cm=40, width_cm=30, height_cm=20,
          is_active=False)
        A(product=cls.full, label="Slot B", slot="B", length_cm=40, width_cm=30, height_cm=25,
          pack_l_cm=20, pack_w_cm=15, pack_h_cm=12)
        A(product=cls.full, label="Slot C", slot="C", length_cm=60, width_cm=40, height_cm=30,
          unit_l_cm=6, unit_w_cm=6, unit_h_cm=12)
        cls.promo_user = _user("promotor", GROUP_OPTIMIZER)
        CartonPromotion.objects.create(product=cls.full, version=2, dims="45×40×30",
                                       source_label="Płaski", fill_before=25, fill_after=80,
                                       user=cls.promo_user)
        cls.issue = PackagingIssue.objects.create(ref_code="CV-FULL",
                                                  issue_type="carton_underfilled",
                                                  product=cls.full)

        # Materiał bez instrukcji — warianty liczone z instr=None, slot B bez hierarchii.
        cls.noinstr = P(code="CV-NOINSTR", name="Bez instrukcji")
        A(product=cls.noinstr, label="Samotny", length_cm=40, width_cm=30, height_cm=20)
        A(product=cls.noinstr, label="Samotny B", slot="B", length_cm=40, width_cm=30,
          height_cm=20)

        # Materiał z instrukcją, bez wymiarów sztuki i bez wariantów, max 220 (= limit).
        cls.bare = P(code="CV-BARE", name="Goły")
        _instr(cls.bare, carton_l=40, carton_w=30, carton_h=25, max_height_total_cm=220,
               demand_pcs=0, pcs_per_carton=0)

        # Materiał z przeliczonymi layoutami → build_hierarchy daje 3D palety (pallet_three).
        # OR-Tools (limit czasu 3 s) wyłączony, żeby wynik nie zależał od szybkości runnera.
        cls.laid = P(code="CV-LAID", name="Z układem")
        laid_instr = _instr(cls.laid, carton_l=40, carton_w=30, carton_h=25)
        from ui.views.core.packing import _recalculate_instruction
        with patch("ui.views.core.packing_core._optimal_layout_option", return_value=None):
            _recalculate_instruction(laid_instr)

        # Inny materiał + jego zgłoszenie (sprawdza filtr zgłoszenia po materiale).
        cls.other = P(code="CV-OTHER", name="Inny materiał")
        cls.other_issue = PackagingIssue.objects.create(ref_code="CV-OTHER",
                                                        issue_type="carton_fit_pallet",
                                                        product=cls.other)
        # Wyszukiwarka: nieaktywny nie pojawia się w podpowiedziach.
        P(code="CV-INACTIVE", name="Wycofany wariantowy", is_active=False)

    def setUp(self):
        self.client.force_login(self.op)

    def _get(self, params=None):
        resp = self.client.get(reverse(URL), params or {})
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "ui/carton_opt/variants.html")
        return resp

    def _scenarios(self):
        f = "CV-FULL"
        return {
            "no_query": {},
            "blank_query": {"product": "   "},
            "query_no_match": {"product": "ZZZ-NIC"},
            "query_suggestions_by_name": {"product": "wariantowy"},
            "query_suggestions_by_code": {"product": "CV-"},
            "full_default": {"product": f},
            "full_lowercase_ref": {"product": "cv-full"},
            "full_by_ean": {"product": "5901234123457"},
            "full_annual_custom": {"product": f, "annual": "5000"},
            "full_annual_zero": {"product": f, "annual": "0"},
            "full_annual_invalid": {"product": f, "annual": "abc"},
            "full_annual_float": {"product": f, "annual": "1.5"},
            "full_annual_negative": {"product": f, "annual": "-10"},
            "full_suggest": {"product": f, "suggest": "1"},
            "full_issue_own": {"product": f, "issue": str(self.issue.pk)},
            "full_issue_other_product": {"product": f, "issue": str(self.other_issue.pk)},
            "full_issue_not_digit": {"product": f, "issue": "x1"},
            "full_issue_missing": {"product": f, "issue": "999999"},
            "noinstr_default": {"product": "CV-NOINSTR", "annual": "5000", "suggest": "1"},
            "noinstr_issue": {"product": "CV-NOINSTR", "issue": str(self.other_issue.pk)},
            "bare_default": {"product": "CV-BARE", "suggest": "1"},
            "laid_default": {"product": "CV-LAID"},
            "no_product_with_issue": {"issue": str(self.issue.pk), "suggest": "1",
                                      "annual": "100"},
        }

    def test_context_matches_snapshot(self):
        got = {name: _normalize(self._get(params).context)
               for name, params in self._scenarios().items()}
        if os.environ.get("CARTON_OPT_VARIANTS_SNAPSHOT_UPDATE"):
            SNAPSHOT.parent.mkdir(exist_ok=True)
            SNAPSHOT.write_text(json.dumps(got, indent=1, sort_keys=True, ensure_ascii=False),
                                encoding="utf-8")
        expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(sorted(got), sorted(expected))
        for name in expected:
            with self.subTest(scenario=name):
                self.assertEqual(got[name], expected[name])

    def test_issue_of_other_product_is_shown(self):
        # PODEJRZANE (przypięte, nie naprawiane): ?issue= filtruje tylko po pk, NIE po
        # materiale — zgłoszenie innego materiału trafia do kontekstu warsztatu. Promocja
        # (carton_opt_variant_promote) filtruje już po product=alt.product.
        resp = self._get({"product": "CV-FULL", "issue": str(self.other_issue.pk)})
        self.assertEqual(resp.context["issue"].ref_code, "CV-OTHER")

    def test_annual_ignored_without_instruction(self):
        resp = self._get({"product": "CV-NOINSTR", "annual": "5000"})
        self.assertEqual(resp.context["annual"], 0)
        self.assertEqual(resp.context["variant_b"].label, "Samotny B")
        self.assertIsNone(resp.context["hier_b"])

    def test_invalid_annual_falls_back_to_demand(self):
        resp = self._get({"product": "CV-FULL", "annual": "abc"})
        self.assertEqual(resp.context["annual"], 100000)

    def test_rows_sorted_by_fill_none_last_inactive_hidden(self):
        rows = self._get({"product": "CV-FULL"}).context["rows"]
        labels = [r["alt"].label for r in rows]
        self.assertNotIn("Ukryty", labels)
        self.assertEqual(labels[-1], "Gigant")           # nie mieści się → fill None na końcu
        self.assertIsNone(rows[-1]["fill"])
        fills = [r["fill"] for r in rows if r["fill"] is not None]
        self.assertEqual(fills, sorted(fills, reverse=True))


class CartonOptVariantsAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.p = Product.objects.create(code="CV-ACC", name="Dostęp")
        _instr(cls.p)

    def _status(self, user):
        if user:
            self.client.force_login(user)
        return self.client.get(reverse(URL), {"product": "CV-ACC"})

    def test_anonymous_redirected_to_login(self):
        resp = self._status(None)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_viewer_forbidden(self):
        resp = self._status(_user("v", GROUP_VIEWER))
        self.assertEqual(resp.status_code, 403)
        self.assertTemplateUsed(resp, "ui/403.html")

    def test_allowed_groups_and_superuser(self):
        for i, grp in enumerate((GROUP_OPTIMIZER, GROUP_MASTER_DATA, GROUP_ADMIN)):
            with self.subTest(group=grp):
                self.assertEqual(self._status(_user(f"u{i}", grp)).status_code, 200)
        su = get_user_model().objects.create_superuser("root", "r@x.pl", "x")
        self.assertEqual(self._status(su).status_code, 200)

    def test_page_renders_search_without_product(self):
        self.client.force_login(_user("op2", GROUP_OPTIMIZER))
        resp = self.client.get(reverse(URL))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["product"])
        self.assertEqual(resp.context["rows"], [])
