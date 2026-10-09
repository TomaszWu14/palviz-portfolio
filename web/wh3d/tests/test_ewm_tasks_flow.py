"""Zadania EWM (WT) w animacji przepływów: ruchy wózków z realnych zadań (agenci z zasobów,
czasy od znaczników potwierdzenia, lokalizacje spoza modelu pominięte), endpoint sceny
(okno czasu, limit, uprawnienia) oraz ekrany importu (podgląd → import → raport)."""
import shutil
import tempfile
from datetime import datetime, timedelta, timezone as dt_tz
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ui.models import WarehouseHallFeature, WarehouseModel, WarehouseModelRack
from ui.roles import GROUP_MASTER_DATA
from wh3d.blender_scene import build_scene
from wh3d.blender_stock import SlotLocator
from wh3d.blender_tasks import resolve_moves
from wh3d.models import WarehouseTask, WarehouseTaskBatch
from wh3d.tests.test_ewm_tasks_refresh import META_REFRESH

T0 = datetime(2026, 3, 2, 6, 0, tzinfo=dt_tz.utc)
TMP_MEDIA = tempfile.mkdtemp(prefix="wt-media-")


def _racks():
    return [{"id": i, "zone": "B0", "rack_id": f"0{i}", "x": 4.0, "y": y, "angle": 0.0, "width": 10.0,
             "depth": 1.1, "level_h": 1.5, "n_bays": 4, "n_levels": 3} for i, y in ((1, 3.0), (2, 8.0))]


def _row(minutes, kind, src, dst, resource="WOZEK01", user="JKOWAL"):
    return (T0 + timedelta(minutes=minutes), resource, user, kind, src, dst, "M1")


class ResolveMovesTests(SimpleTestCase):
    def setUp(self):
        self.loc = SlotLocator(_racks(), ["B0-01-100A", "B0-01-300A", "B0-02-100A"])

    def test_endpoints_by_kind_and_unmapped_skipped(self):
        rows = [_row(0, "putaway", "GR-ZONE", "B0-01-100A"),        # dok → gniazdo
                _row(5, "outbound", "B0-02-100A", "GI-ZONE"),        # gniazdo → dok
                _row(6, "picking", "B0-01-300A", "PACK-01"),         # gniazdo → stanowisko
                _row(7, "move", "B0-01-100A", "GI-ZONE"),            # przesunięcie poza model
                _row(8, "putaway", "GR-ZONE", "C9-99-999A")]         # regał spoza modelu
        moves, skipped = resolve_moves(rows, self.loc, T0)
        self.assertEqual(skipped, 2)
        self.assertEqual([m[2] for m in moves], ["putaway", "outbound", "picking"])
        self.assertEqual(moves[0][3], ("dock",))
        self.assertEqual(moves[0][4][0], "rack")
        self.assertEqual(moves[1][4], ("dock",))
        self.assertEqual(moves[2][4], ("station",))

    def test_times_from_confirmation_with_compression(self):
        rows = [_row(0, "putaway", "", "B0-01-100A"), _row(10, "putaway", "", "B0-01-100A")]
        self.assertEqual([m[0] for m in resolve_moves(rows, self.loc, T0)[0]], [0.0, 600.0])
        self.assertEqual([m[0] for m in resolve_moves(rows, self.loc, T0, scale=20)[0]], [0.0, 30.0])

    def test_agent_is_resource_or_user(self):
        rows = [_row(0, "putaway", "", "B0-01-100A", resource=""), _row(1, "putaway", "", "B0-01-100A")]
        self.assertEqual([m[1] for m in resolve_moves(rows, self.loc, T0)[0]], ["JKOWAL", "WOZEK01"])


class SceneFromTasksTests(SimpleTestCase):
    def _scene(self, rows, **kw):
        racks = _racks()
        loc = SlotLocator(racks, ["B0-01-100A", "B0-01-300A", "B0-02-100A"])
        moves, _ = resolve_moves(rows, loc, T0, **kw)
        feats = [{"kind": "dock", "x": 5, "y": 17, "width": 3, "depth": 2, "angle": 0}]
        return build_scene({"id": 1, "name": "Hala"}, {"width": 30, "depth": 20}, racks, feats,
                           moves=moves, demo_pickers=0)

    def test_agents_from_resources_no_demo_forklifts(self):
        sc = self._scene([_row(0, "putaway", "GR", "B0-01-100A"), _row(1, "outbound", "B0-02-100A", "GI"),
                          _row(2, "putaway", "GR", "B0-01-300A", resource="WOZEK02")])
        self.assertEqual(sc["source"]["forklifts"], "ewm_tasks")
        trucks = [a for a in sc["agents"] if a["kind"] == "forklift"]
        self.assertEqual([a["label"] for a in trucks], ["WOZEK01", "WOZEK02"])
        self.assertEqual(len([i for i in sc["items"] if i["kind"] == "pallet"]), 3)
        self.assertEqual({f["kind"] for f in sc["flows"]}, {"inbound", "outbound"})

    def test_task_starts_at_confirmation_and_times_monotonic(self):
        sc = self._scene([_row(0, "putaway", "GR", "B0-01-100A"), _row(30, "replenishment", "B0-01-300A", "B0-02-100A")])
        kfs = sc["agents"][0]["keyframes"]
        ts = [k["t"] for k in kfs]
        self.assertEqual(ts, sorted(ts))
        self.assertIn(1800.0, ts)                          # czeka do potwierdzenia 2. zadania
        waiting = next(k for k in kfs if k["t"] == 1800.0)
        before = kfs[kfs.index(waiting) - 1]
        self.assertEqual((before["x"], before["y"]), (waiting["x"], waiting["y"]))   # postój, nie jazda
        self.assertGreater(sc["duration"], 1800.0)
        self.assertIn("replenishment", {f["kind"] for f in sc["flows"]})
        self.assertIn("replenishment", sc["flow_colors"])

    def test_busy_agent_starts_next_task_right_after(self):
        sc = self._scene([_row(0, "putaway", "GR", "B0-01-100A"), _row(0, "putaway", "GR", "B0-02-100A")])
        pallets = [i for i in sc["items"] if i["kind"] == "pallet"]
        self.assertGreater(pallets[1]["keyframes"][-1]["t"], pallets[0]["keyframes"][-1]["t"])


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class TasksEndpointAndImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.md = User.objects.create_user("md", password="x")
        cls.md.groups.add(Group.objects.get_or_create(name=GROUP_MASTER_DATA)[0])
        cls.plain = User.objects.create_user("plain", password="x")
        cls.wm = WarehouseModel.objects.create(name="Hala", floor_width_m=30, floor_depth_m=20)
        for i, y in ((1, 3), (2, 8)):
            WarehouseModelRack.objects.create(model=cls.wm, zone="B0", rack_id=f"0{i}", n_bays=4, n_levels=3, x_m=4, y_m=y)
        WarehouseHallFeature.objects.create(model=cls.wm, kind="dock", label="Dok A", x_m=5, y_m=17, width_m=3, depth_m=2)
        cls.batch = WarehouseTaskBatch.objects.create(name="Marzec", status="done", first_confirmed=T0,
                                                      last_confirmed=T0 + timedelta(hours=3), row_count=5)
        for n, (m, kind, src, dst, res) in enumerate([
                (5, "putaway", "GR-ZONE", "B0-01-100A", "WOZEK01"), (10, "outbound", "B0-02-300A", "GI-ZONE", "WOZEK02"),
                (20, "move", "B0-01-100A", "XX-99", "WOZEK01"), (50, "putaway", "GR-ZONE", "B0-02-100A", ""),
                (150, "putaway", "GR-ZONE", "B0-01-200A", "WOZEK03")]):
            WarehouseTask.objects.create(batch=cls.batch, task_no=str(n), kind=kind, src_location=src, dst_location=dst,
                                         resource=res, user="JKOWAL", confirmed_at=T0 + timedelta(minutes=m))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)     # tylko własny katalog — override już zdjęty

    def _flow(self, **params):
        return self.client.get(reverse("ui:warehouse_model_flow_json", args=[self.wm.pk]), params)

    # ── endpoint sceny ──────────────────────────────────────────────────────
    def test_latest_batch_default_window_first_hour(self):
        self.client.force_login(self.md)
        src = self._flow(wt="latest", pallets="0").json()["source"]
        self.assertEqual(src["forklifts"], "ewm_tasks")
        self.assertEqual(src["tasks_batch"], "Marzec")
        self.assertEqual(src["tasks"]["total"], 4)            # 5. zadanie (06:00+150 min) poza 1. godziną
        self.assertEqual((src["tasks"]["animated"], src["tasks"]["skipped_unmapped"]), (3, 1))
        self.assertFalse(src["tasks"]["truncated"])
        start = timezone.localtime(T0).strftime("%Y-%m-%dT%H:%M")
        self.assertEqual(src["window"]["from"], start)

    def test_agents_are_resources_with_user_fallback(self):
        self.client.force_login(self.md)
        sc = self._flow(wt=str(self.batch.pk), pallets="0").json()
        self.assertEqual({a["label"] for a in sc["agents"] if a["kind"] == "forklift"},
                         {"WOZEK01", "WOZEK02", "JKOWAL"})

    def test_window_param_hours_and_scale(self):
        self.client.force_login(self.md)
        start = timezone.localtime(T0 + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
        src = self._flow(wt="latest", wt_from=start, wt_hours="1", wt_scale="10", pallets="0").json()["source"]
        self.assertEqual(src["tasks"]["total"], 1)
        self.assertEqual(src["time_scale"], 10.0)

    def test_limit_truncates_with_flag(self):
        self.client.force_login(self.md)
        with mock.patch("wh3d.blender_tasks.MAX_TASKS", 2):
            tasks = self._flow(wt="latest", pallets="0").json()["source"]["tasks"]
        self.assertEqual((tasks["loaded"], tasks["total"], tasks["truncated"]), (2, 4, True))

    def test_demo_fallback_and_unfinished_batch_404(self):
        self.client.force_login(self.md)
        self.assertEqual(self._flow(wt="", pallets="0").json()["source"]["forklifts"], "demo")
        queued = WarehouseTaskBatch.objects.create(name="W kolejce")
        self.assertEqual(self._flow(wt=str(queued.pk)).status_code, 404)

    def test_model_view_lists_task_batches(self):
        self.client.force_login(self.md)
        html = self.client.get(reverse("ui:warehouse_model_view", args=[self.wm.pk]), {"wt": self.batch.pk}).content.decode()
        self.assertIn('data-f="wt"', html)
        self.assertIn(f'value="{self.batch.pk}"', html)
        self.assertIn("Zadania EWM: Marzec", html)
        self.assertIn(reverse("ui:ewm_tasks_list"), html)
        self.assertNotIn("{# Animacja", html)              # wieloliniowy {# #} nie wycieka jako tekst

    # ── ekrany importu ──────────────────────────────────────────────────────
    CSV = ("Zadanie magazynowe;Rodzaj procesu magazynowego;Źródłowe miejsce składowania;Docelowe miejsce składowania;"
           "Data potwierdzenia;Czas potwierdzenia;Zasób\n"
           "1;1010;GR-ZONE;B0-01-100A;02.03.2026;06:10:00;WOZEK01\n"
           "2;2010;B0-02-100A;GI-ZONE;02.03.2026;06:20:00;WOZEK02\n"
           "3;2010;;;02.03.2026;06:30:00;WOZEK02\n")

    def _preview(self, text=None, **extra):
        f = SimpleUploadedFile("wt.csv", (text or self.CSV).encode("utf-8"), content_type="text/csv")
        return self.client.post(reverse("ui:ewm_tasks_preview"), {"file": f, "tz": "UTC", **extra})

    def test_import_views_require_master_data(self):
        self.client.force_login(self.plain)
        self.assertNotEqual(self.client.get(reverse("ui:ewm_tasks_list")).status_code, 200)
        self.assertNotEqual(self._preview().status_code, 200)
        self.assertFalse(WarehouseTaskBatch.objects.exclude(pk=self.batch.pk).exists())

    def test_preview_then_import_then_report(self):
        self.client.force_login(self.md)
        self.assertEqual(self.client.get(reverse("ui:ewm_tasks_list")).status_code, 200)
        r = self._preview(kind_map="2010 = kompletacja")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WarehouseTaskBatch.objects.count(), 1)          # podgląd nic nie zapisuje
        ctx = r.context
        self.assertEqual((ctx["stats"]["imported"], ctx["stats"]["errors"]), (2, 1))
        self.assertContains(r, "Źródłowe miejsce składowania")
        self.assertContains(r, "Kompletacja")
        r = self.client.post(reverse("ui:ewm_tasks_import"), {
            "token": ctx["token"], "file_name": "wt.csv", "tz": "UTC", "kind_map": "2010 = kompletacja", "name": "Test"})
        batch = WarehouseTaskBatch.objects.get(name="Test")
        self.assertRedirects(r, reverse("ui:ewm_tasks_detail", args=[batch.pk]))
        self.assertEqual((batch.status, batch.row_count, batch.error_count), ("done", 2, 1))
        self.assertEqual(sorted(batch.tasks.values_list("kind", flat=True)), ["picking", "putaway"])
        self.assertEqual(batch.first_confirmed, datetime(2026, 3, 2, 6, 10, tzinfo=dt_tz.utc))
        r = self.client.get(reverse("ui:ewm_tasks_detail", args=[batch.pk]), {"model": self.wm.pk})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.context["report"]["mapped"], r.context["report"]["unmapped"]), (2, 2))
        self.assertContains(r, "GI-ZONE")
        # token jednorazowy — plik skasowany po imporcie
        again = self.client.post(reverse("ui:ewm_tasks_import"), {"token": ctx["token"]})
        self.assertRedirects(again, reverse("ui:ewm_tasks_list"), fetch_redirect_response=False)

    def test_large_file_goes_to_celery(self):
        self.client.force_login(self.md)
        token = self._preview().context["token"]
        with mock.patch("wh3d.views.warehouse_tasks.SYNC_MAX_BYTES", 0),                 mock.patch("wh3d.views.warehouse_tasks.import_warehouse_tasks") as task:
            self.client.post(reverse("ui:ewm_tasks_import"), {"token": token, "file_name": "wt.csv"})
        batch = WarehouseTaskBatch.objects.latest("pk")
        task.apply_async.assert_called_once_with((batch.pk, token), retry=False)
        self.assertEqual(batch.status, "queued")
        self.assertContains(self.client.get(reverse("ui:ewm_tasks_detail", args=[batch.pk])), "Import trwa w tle")

    def test_no_broker_falls_back_to_background_thread(self):
        """Produkcja bez Redisa: apply_async rzuca → import w wątku zamiast 500 i wiszącej partii."""
        from kombu.exceptions import OperationalError

        class InlineThread:                                 # wątek „od razu” — deterministyczny test
            def __init__(self, target, args, daemon):
                self.target, self.args = target, args

            def start(self):
                with mock.patch("wh3d.views.warehouse_tasks.connection"):
                    self.target(*self.args)

        self.client.force_login(self.md)
        token = self._preview().context["token"]
        with mock.patch("wh3d.views.warehouse_tasks.SYNC_MAX_BYTES", 0),                 mock.patch("wh3d.views.warehouse_tasks.import_warehouse_tasks") as task,                 mock.patch("wh3d.views.warehouse_tasks.threading.Thread", InlineThread):
            task.apply_async.side_effect = OperationalError("Error 111 connecting to 127.0.0.1:6379")
            r = self.client.post(reverse("ui:ewm_tasks_import"), {"token": token, "file_name": "wt.csv"})
        batch = WarehouseTaskBatch.objects.latest("pk")
        self.assertRedirects(r, reverse("ui:ewm_tasks_detail", args=[batch.pk]))
        self.assertEqual((batch.status, batch.row_count), ("done", 2))

    def test_stuck_background_import_marked_stale(self):
        self.client.force_login(self.md)
        batch = WarehouseTaskBatch.objects.create(name="Wisi", status="running")
        WarehouseTaskBatch.objects.filter(pk=batch.pk).update(uploaded_at=timezone.now() - timedelta(hours=7))
        r = self.client.get(reverse("ui:ewm_tasks_detail", args=[batch.pk]))
        self.assertContains(r, "Import nie skończył się")
        self.assertNotRegex(r.content.decode(), META_REFRESH)

    def test_missing_columns_block_import_and_bad_token_rejected(self):
        self.client.force_login(self.md)
        r = self._preview("Produkt;Partia\nM1;L1\n")
        self.assertContains(r, "Nie rozpoznano wymaganych kolumn")
        self.assertNotContains(r, "Importuj cały plik")
        r = self.client.post(reverse("ui:ewm_tasks_import"), {"token": "../../settings.py"})
        self.assertRedirects(r, reverse("ui:ewm_tasks_list"), fetch_redirect_response=False)

    def test_delete_batch_removes_tasks(self):
        self.client.force_login(self.md)
        self.client.post(reverse("ui:ewm_tasks_delete", args=[self.batch.pk]))
        self.assertFalse(WarehouseTask.objects.exists())
