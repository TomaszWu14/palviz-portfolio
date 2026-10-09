"""Testy regresji naprawionych błędów importu master daty (formaty MARM i kolumn łączonych).

1. MARM: brak wagi SZT → waga OP / sztuk w OP (spójnie ze ścieżką KAR).
2. Reimport: puste pole w pliku nie zeruje istniejącego EAN / dostawcy.
3. „Nadpisz” kasuje tylko opakowania *-opz utworzone przez import, nie ręczne.
4. Duplikaty kodów: zawsze wygrywa PIERWSZY wiersz, liczba duplikatów w komunikacie.
5. Limit ``MAX_IMPORT_ROWS``.
6. Purge (zakres inner_packs / all) kasuje tylko opakowania *-opz z importu (reguła z 3).
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from ui.models import Carton, InnerPack, PalletizationInstruction, Product
from ui.roles import GROUP_MASTER_DATA
from ui.tests.test_master_import import HEADER, MARM_HEADER, _marm, _row

URL = "ui:planner_master_data_import"


class MasterDataImportRegressionTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_user(username="md-reg", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    def _post(self, header, rows, **extra):
        content = (header + "\n" + "\n".join(rows)).encode("utf-8")
        data = {"file": SimpleUploadedFile("m.csv", content, content_type="text/csv"), **extra}
        resp = self.client.post(reverse(URL), data)
        self.assertEqual(resp.status_code, 302)
        return [(m.level_tag, m.message) for m in get_messages(resp.wsgi_request)]

    def _instr(self, code):
        return PalletizationInstruction.objects.get(product__code=code, name="Import migracji")

    # 1 ─ waga sztuki z OP
    def test_marm_weight_from_op_is_divided_by_op_pieces(self):
        self._post(MARM_HEADER, [
            _marm("W-OP", "OP", "1", "10", "6", "4", "5", "0,2"),
            _marm("W-OP", "KAR", "1", "100", "40", "30", "25", "20"),
        ])
        self.assertAlmostEqual(self._instr("W-OP").unit_weight, 0.02)   # 0,2 kg / 10 szt.

    def test_marm_weight_op_and_kar_paths_consistent(self):
        # Ta sama sztuka (0,02 kg): raz tylko przez OP, raz tylko przez KAR.
        self._post(MARM_HEADER, [
            _marm("W-A", "OP", "1", "10", "6", "4", "5", "0,2"),
            _marm("W-A", "KAR", "1", "100", "40", "30", "25"),
            _marm("W-B", "KAR", "1", "100", "40", "30", "25", "2"),
        ])
        self.assertAlmostEqual(self._instr("W-A").unit_weight, self._instr("W-B").unit_weight)

    # 2 ─ reimport nie zeruje EAN / dostawcy
    def test_marm_reimport_keeps_existing_ean_and_supplier(self):
        Product.objects.create(code="KEEP-1", name="stary", ean="5900000000001",
                               supplier_short="SUP")
        self._post(MARM_HEADER, [_marm("KEEP-1", "SZT", "1", "1", "3", "3", "3")])
        p = Product.objects.get(code="KEEP-1")
        self.assertEqual((p.ean, p.supplier_short), ("5900000000001", "SUP"))

    def test_reimport_with_value_still_overwrites(self):
        Product.objects.create(code="KEEP-2", name="stary", ean="111", supplier_short="OLD")
        self._post(HEADER, [_row("KEEP-2", "nowy", "5 X 5 X 5 cm", "", "", producent="NEW")])
        p = Product.objects.get(code="KEEP-2")
        self.assertEqual((p.name, p.ean, p.supplier_short), ("nowy", "111", "NEW"))

    # 3 ─ „Nadpisz” nie kasuje ręcznych opakowań *-opz
    def test_overwrite_keeps_manual_opz_packs(self):
        opz = dict(op_dim="6 X 4 X 5 cm", op_qty="12", opz_dim="20 X 15 X 11 cm", opz_qty="36")
        self._post(HEADER, [_row("OW-1", "opz", "2 X 2 X 5 cm", "40 X 30 X 25 cm", "240", **opz)])
        manual = InnerPack.objects.create(name="RECZNY-opz", length_cm=1, width_cm=1, height_cm=1)
        # Ręczne opakowanie o nazwie jak z importu, ale bez instrukcji importu.
        orphan = InnerPack.objects.create(name="OW-9-opz", length_cm=1, width_cm=1, height_cm=1)
        # Opakowanie z importu, ale użyte też w ręcznym kartonie → zostaje.
        self._post(HEADER, [_row("OW-2", "opz", "2 X 2 X 5 cm", "40 X 30 X 25 cm", "240", **opz)])
        shared = InnerPack.objects.get(name="OW-2-opz")
        Carton.objects.create(name="Ręczny karton", length_cm=40, width_cm=30, height_cm=25,
                              unit_weight_kg=1, inner_pack=shared)

        self._post(HEADER, [_row("OW-3", "nowy", "5 X 5 X 5 cm", "30 X 20 X 20 cm", "8")],
                   overwrite="1")
        names = set(InnerPack.objects.values_list("name", flat=True))
        self.assertNotIn("OW-1-opz", names)                  # z importu → skasowane
        self.assertTrue(InnerPack.objects.filter(pk=manual.pk).exists())
        self.assertTrue(InnerPack.objects.filter(pk=orphan.pk).exists())
        self.assertTrue(InnerPack.objects.filter(pk=shared.pk).exists())
        self.assertEqual(PalletizationInstruction.objects.filter(name="Import migracji").count(), 1)

    # 4 ─ duplikaty: pierwszy wiersz wygrywa, zgłoszone w komunikacie
    def test_format_b_duplicates_first_wins_and_reported(self):
        long = "D" * 50
        msgs = self._post(HEADER, [
            _row("DUP-1", "pierwszy", "", "30 X 20 X 20 cm", "10"),
            _row("DUP-1", "drugi", "", "30 X 20 X 20 cm", "99"),
            _row(long + "X", "długi pierwszy", "", "30 X 20 X 20 cm", "11"),
            _row(long + "Y", "długi drugi", "", "30 X 20 X 20 cm", "12"),
        ])
        self.assertEqual(Product.objects.get(code="DUP-1").name, "pierwszy")
        self.assertEqual(self._instr("DUP-1").pcs_per_carton, 10)
        self.assertEqual(Product.objects.get(code=long).name, "długi pierwszy")
        self.assertEqual(self._instr(long).pcs_per_carton, 11)
        warnings = [m for lvl, m in msgs if lvl == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("Pominięto 2 zduplikowanych kodów", warnings[0])

    def test_no_duplicate_warning_for_clean_file(self):
        msgs = self._post(HEADER, [_row("CLEAN-1", "a", "", "30 X 20 X 20 cm", "10")])
        self.assertEqual([lvl for lvl, _ in msgs], ["success"])

    # 5 ─ limit wierszy
    def test_row_limit_rejects_file(self):
        rows = [_row(f"LIM-{i}", "x", "", "30 X 20 X 20 cm", "1") for i in range(3)]
        with mock.patch("ui.views.products_import_md.MAX_IMPORT_ROWS", 2):
            msgs = self._post(HEADER, rows)
        self.assertEqual(msgs, [("error", "Plik zbyt duży (max 2 wierszy).")])
        self.assertFalse(Product.objects.exists())

    def test_row_limit_allows_exact_limit(self):
        rows = [_row(f"LIM-{i}", "x", "", "30 X 20 X 20 cm", "1") for i in range(2)]
        with mock.patch("ui.views.products_import_md.MAX_IMPORT_ROWS", 2):
            self._post(HEADER, rows)
        self.assertEqual(Product.objects.count(), 2)


class MarmLimitAndCounterTests(TestCase):
    """7. Limit MAX_IMPORT_ROWS dla MARM liczony po MATERIAŁACH (kilka wierszy na materiał:
       SZT/OP/OPZ/KAR/PAZ/JU) — 60 000 wierszy to było tylko ok. 12–15 tys. materiałów;
       twardy limit wierszy = limit × 10 (pamięć workera). Format łączony bez zmian.
    8. Licznik/mianownik niecałkowity („1.991” — kropka dziesiętna, jak w eksporcie) jest
       zaokrąglany jak dotąd, ale import mówi o tym w ostrzeżeniu (dawniej po cichu)."""

    def setUp(self):
        u = get_user_model().objects.create_user(username="md-marm", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)

    def _post(self, rows):
        content = (MARM_HEADER + "\n" + "\n".join(rows)).encode("utf-8")
        resp = self.client.post(reverse(URL), {"file": SimpleUploadedFile("m.csv", content)})
        self.assertEqual(resp.status_code, 302)
        return [(m.level_tag, m.message) for m in get_messages(resp.wsgi_request)]

    @staticmethod
    def _material(code):
        return [_marm(code, "SZT", "1", "1", "3", "3", "3", "0,1"),
                _marm(code, "KAR", "1", "10", "30", "20", "20", "1")]

    def test_marm_limit_counts_materials_not_rows(self):
        rows = [r for i in range(3) for r in self._material(f"ML-{i}")]      # 6 wierszy, 3 mat.
        with mock.patch("ui.views.products_import_md.MAX_IMPORT_ROWS", 4):
            msgs = self._post(rows)
        self.assertEqual(Product.objects.filter(code__startswith="ML-").count(), 3)
        self.assertEqual([lvl for lvl, _m in msgs], ["success"])

    def test_marm_over_material_limit_rejected(self):
        rows = [r for i in range(5) for r in self._material(f"MX-{i}")]
        with mock.patch("ui.views.products_import_md.MAX_IMPORT_ROWS", 4):
            msgs = self._post(rows)
        self.assertEqual(msgs, [("error", "Plik zbyt duży (max 4 materiałów MARM).")])
        self.assertFalse(Product.objects.exists())

    def test_marm_hard_row_cap(self):
        rows = [_marm("ONE", "SZT", "1", "1", "3", "3", "3")] * 41          # 1 materiał, 41 wierszy
        with mock.patch("ui.views.products_import_md.MAX_IMPORT_ROWS", 4):
            msgs = self._post(rows)
        self.assertEqual(msgs, [("error", "Plik zbyt duży (max 40 wierszy MARM).")])

    def test_fractional_counter_is_reported(self):
        msgs = self._post([_marm("FR-1", "OP", "1", "1.991", "21", "12", "5,5", "0,458"),
                           _marm("FR-1", "KAR", "1", "10", "29", "25", "22", "4,583")])
        warn = [m for lvl, m in msgs if lvl == "warning"]
        self.assertEqual(len(warn), 1)
        self.assertIn("FR-1 OP: licznik 1.991 → 2", warn[0])

    def test_integer_counters_no_warning(self):
        msgs = self._post(self._material("INT-1"))
        self.assertEqual([lvl for lvl, _m in msgs], ["success"])


class MasterDataPurgeOpzTests(TestCase):
    """„Usuń opakowania zbiorcze z importu” (purge, zakres inner_packs / all) kasuje tylko
    opakowania *-opz utworzone przez import — tę samą regułę co „Nadpisz” (3)."""

    def setUp(self):
        u = get_user_model().objects.create_user(username="md-purge", password="x")
        u.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        self.client.force_login(u)
        opz = dict(op_dim="6 X 4 X 5 cm", op_qty="12", opz_dim="20 X 15 X 11 cm", opz_qty="36")
        rows = [_row(c, "opz", "2 X 2 X 5 cm", "40 X 30 X 25 cm", "240", **opz)
                for c in ("PU-1", "PU-2")]
        content = (HEADER + "\n" + "\n".join(rows)).encode("utf-8")
        self.client.post(reverse(URL), {"file": SimpleUploadedFile("m.csv", content)})
        self.manual = InnerPack.objects.create(name="RECZNY-opz", length_cm=1, width_cm=1,
                                               height_cm=1)
        self.orphan = InnerPack.objects.create(name="PU-9-opz", length_cm=1, width_cm=1,
                                               height_cm=1)
        self.shared = InnerPack.objects.get(name="PU-2-opz")
        Carton.objects.create(name="Ręczny karton", length_cm=40, width_cm=30, height_cm=25,
                              unit_weight_kg=1, inner_pack=self.shared)

    def _purge(self, scope):
        resp = self.client.post(reverse("ui:planner_master_data_purge"), {"scope": scope})
        self.assertEqual(resp.status_code, 302)
        return [m.message for m in get_messages(resp.wsgi_request)]

    def _assert_only_import_pack_deleted(self):
        names = set(InnerPack.objects.values_list("name", flat=True))
        self.assertNotIn("PU-1-opz", names)                     # z importu → skasowane
        self.assertEqual(names, {"RECZNY-opz", "PU-9-opz", "PU-2-opz"})

    def test_inner_packs_scope_keeps_manual_and_shared(self):
        msgs = self._purge("inner_packs")
        self._assert_only_import_pack_deleted()
        self.assertIn("Usunięto: 1 opakowań zbiorczych z importu.", msgs)

    def test_all_scope_deletes_import_packs_before_instructions(self):
        # Zakres „all” kasuje instrukcje importu — opakowania „z importu” trzeba ustalić
        # WCZEŚNIEJ, bo reguła opiera się na powiązaniu z instrukcją „Import migracji”.
        self._purge("all")
        self._assert_only_import_pack_deleted()
        self.assertFalse(PalletizationInstruction.objects.filter(name="Import migracji").exists())
