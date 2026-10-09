"""Macierz REF × jednostki (biblioteka grafik). Stan komórki musi zgadzać się z tym,
które poziomy build_hierarchy uznaje za używane, i śledzić obecność renderu."""
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from ui.models import Product, PalletizationInstruction, Carton, CartonArtwork


def _carton(name="K"):
    return Carton.objects.create(name=name, length_cm=40, width_cm=30, height_cm=25,
                                 unit_weight_kg=0.5, pieces_per_carton=10)


def _product(code, carton, with_unit_dims=True):
    p = Product.objects.create(
        code=code, name="X",
        unit_length_cm=10 if with_unit_dims else None,
        unit_width_cm=8 if with_unit_dims else None,
        unit_height_cm=5 if with_unit_dims else None)
    PalletizationInstruction.objects.create(
        product=p, version=1, is_active=True, unit_weight=0.5, pcs_per_carton=10,
        carton=carton, carton_l=40, carton_w=30, carton_h=25,
        pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15,
        max_height_total_cm=200)
    return p


class PackagingMatrixTest(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "a@a.pl", "x")
        self.client.force_login(User.objects.get(username="admin"))
        self.url = reverse("ui:packaging_matrix")

    def _cells(self, code):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        row = next(r for r in resp.context["rows"] if r["product"].code == code)
        return {c["key"]: c["state"] for c in row["cells"]}

    def test_carton_used_without_render_is_todo(self):
        _product("REF-A", _carton("KA"))
        cells = self._cells("REF-A")
        self.assertEqual(cells["carton"], "todo")           # używany, brak renderu → śliwkowy
        self.assertEqual(cells["unit"], "todo")             # sztuka bez grafiki
        self.assertEqual(cells["inner_pack"], "absent")     # brak OPZ → szary (case à la RnB10001)

    def test_carton_with_artwork_is_done(self):
        c = _carton("KB")
        _product("REF-B", c)
        CartonArtwork.objects.create(
            carton=c, face="front", kind="print",
            image=SimpleUploadedFile("f.png", b"\x89PNG\r\n", content_type="image/png"))
        cells = self._cells("REF-B")
        self.assertEqual(cells["carton"], "done")           # render wgrany → zielony ✓

    def test_absent_levels_for_minimal_ref(self):
        _product("REF-C", _carton("KC"), with_unit_dims=False)
        cells = self._cells("REF-C")
        self.assertEqual(cells["unit"], "absent")           # brak wymiarów sztuki → nieużywane
        self.assertEqual(cells["inner_pack"], "absent")
        # komórka z uploadem niesie URL tylko dla stanu todo
        self.assertEqual(cells["carton"], "todo")

    def test_query_count_does_not_grow_with_rows(self):
        """Strażnik N+1: macierz woła build_hierarchy na każdy wiersz, więc łatwo tu
        wrócić do zapytania-per-produkt. Liczy się nie próg, tylko BRAK wzrostu."""
        c = _carton("KQ")
        for i in range(5):
            _product(f"Q-{i}", c)
        # Rozgrzewka: pierwsze żądanie w minucie niesie +1 zapytanie PresenceMiddleware
        # (stempel last_seen_at, throttling 60 s) — bez niej pomiary few/many się różnią
        # o jedno zapytanie niezależnie od liczby wierszy.
        self.client.get(self.url)
        with CaptureQueriesContext(connection) as few:
            self.assertEqual(self.client.get(self.url).status_code, 200)
        for i in range(5, 20):
            _product(f"Q-{i}", c)
        with CaptureQueriesContext(connection) as many:
            self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(len(many), len(few),
                         f"zapytania rosną z liczbą wierszy: {len(few)} → {len(many)}")

    def test_upload_url_present_only_for_todo(self):
        _product("REF-D", _carton("KD"))
        resp = self.client.get(self.url)
        row = next(r for r in resp.context["rows"] if r["product"].code == "REF-D")
        by_key = {c["key"]: c for c in row["cells"]}
        self.assertIn("upload_url", by_key["carton"])       # todo → klikalny
        self.assertNotIn("upload_url", by_key["inner_pack"])  # absent → nieklikalny

    def test_done_cell_is_clickable_too(self):
        """Szablon podpisuje zielony kafelek „kliknij, aby podmienić" — bez adresu klik
        otwierał wybór pliku i po cichu go wyrzucał."""
        c = _carton("KE")
        _product("REF-E", c)
        CartonArtwork.objects.create(
            carton=c, face="front", kind="print",
            image=SimpleUploadedFile("f.png", b"\x89PNG\r\n", content_type="image/png"))
        resp = self.client.get(self.url)
        row = next(r for r in resp.context["rows"] if r["product"].code == "REF-E")
        cell = next(c for c in row["cells"] if c["key"] == "carton")
        self.assertEqual(cell["state"], "done")
        self.assertTrue(cell.get("upload_url"))

    def test_inactive_instruction_version_is_ignored(self):
        """Prefetch instrukcji jest zawężony do aktywnych — musi dawać to samo, co
        latest_instruction() bez prefetcha, także gdy istnieje nowsza NIEaktywna wersja
        (wersje narastają przy każdym imporcie i nic ich nie kasuje)."""
        c = _carton("KF")
        p = _product("REF-F", c)
        PalletizationInstruction.objects.create(
            product=p, version=99, is_active=False, unit_weight=0.5, pcs_per_carton=10,
            carton=None, carton_l=1, carton_w=1, carton_h=1,          # bez kartonu!
            pallet_length_cm=120, pallet_width_cm=80, pallet_base_height_cm=15,
            max_height_total_cm=200)
        resp = self.client.get(self.url)
        row = next(r for r in resp.context["rows"] if r["product"].code == "REF-F")
        cell = next(c for c in row["cells"] if c["key"] == "carton")
        # Gdyby wygrała nieaktywna v99 (bez FK kartonu), komórka byłaby „used" bez uploadu.
        self.assertEqual(cell["state"], "todo")
        self.assertTrue(cell.get("upload_url"))
        # …a do pamięci nie wciągamy wersji, których i tak nie użyjemy: `.all()` na
        # produkcie z prefetcha zwraca dokładnie to, co pobrał queryset prefetcha.
        self.assertEqual([i.version for i in row["product"].instructions.all()], [1])
