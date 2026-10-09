# Wywoływanie palet do kontroli — plan implementacji

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zamienić read-only listę HU + pojedynczy `hu_control_next` w kolejkę wywoływania palet z auto-przydziałem, świadomą niekompletności przesyłki, z eskalacją i audytem.

**Architecture:** Podejście A — reuse modelu `HandlingUnit` (status/assigned_to/controlled_by/is_priority). Jeden współdzielony helper `_call_queue()` porządkuje kolejkę; „wywołanie" = atomowa rezerwacja pod lockiem. Niekompletność na poziomie `Shipment` (feed SAP). Eskalacja przez `EscalationRoute` + silnik `Task`. Audyt przez rozszerzony `HUStatusEvent`.

**Tech Stack:** Django (app `ui`), SQLite/Postgres, unittest runner (`manage.py test`), Celery (eager w DEBUG).

## Global Constraints

- UI po polsku (labels, verbose_name, komunikaty).
- Nie edytować zastosowanych migracji — dodawać nowe (`makemigrations`).
- Guard widoków dekoratorami z `roles.py` (`_controller`, `_leader`).
- Reużyć helperów: `_controllable(request, qs)`, `_zone_ok(user, hu)`, `_log_status(hu, from, to, user, note)`, `master_data_client`.
- Testy uruchamiać z `web/`:
  `DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.<MODUŁ> -v 2`
- Po każdej zmianie modelu: `PYTHONPATH=.. python manage.py makemigrations ui`.

## Plik → odpowiedzialność

- `web/ui/models.py` — nowe pola (`HandlingUnit.called_at/snooze_until`, `Customer.priority_rank`, `Shipment.picking_complete/_at/picking_eta_*`, `HUStatusEvent.kind`) + model `EscalationRoute`.
- `web/ui/views/hu_control.py` — `_call_queue()`, `hu_call`, `hu_release`, `hu_escalate`, przebudowa `hu_control_next` i hu-mode listy w `planner_stock_contents`.
- `web/ui/urls.py` — nowe URL-e (`hu_call`, `hu_release`, `hu_escalate`, `picking_eta`).
- `web/ui/templates/ui/stock_contents.html` — grupowanie po odbiorcy, akcje wiersza, wsadowo, badge niekompletności.
- `web/ui/templates/ui/scanner/hu_detail.html` — baner niekompletnej przesyłki.
- `web/ui/views/hu.py` (import feedu) — mapowanie kolumny `picking_complete`.
- `web/ui/tests/test_*.py` — po jednym module na task.

---

### Task 1: Helper kolejki `_call_queue` + `Customer.priority_rank`

**Files:**
- Modify: `web/ui/models.py` (Customer — nowe pole)
- Create: `web/ui/migrations/0112_customer_priority_rank.py` (przez makemigrations)
- Modify: `web/ui/views/hu_control.py` (nowy helper `_call_queue`)
- Test: `web/ui/tests/test_call_queue_order.py`

**Interfaces:**
- Produces: `_call_queue(request) -> QuerySet[HandlingUnit]` — posortowana, przefiltrowana strefą/typem, tylko `status in (planned, to_recheck)`, pomija zajęte przez innych i snooze. Kolejność: `-is_priority`, rekontrola, `-customer.is_vip`, `-customer.priority_rank`, short-dated, `shipment.outbound_created_date` ASC, `id`.
- Consumes (dla Task 2/4): ten sam helper.

- [ ] **Step 1: Dodaj pole `priority_rank` do `Customer`**

W `web/ui/models.py`, w klasie `Customer` obok `is_vip`:
```python
    priority_rank = models.PositiveSmallIntegerField(
        default=0, verbose_name="Ranga priorytetu",
        help_text="Wyższa = wcześniej w kolejce kontroli (tie-break po VIP).")
```

- [ ] **Step 2: Migracja**

Run: `cd web && PYTHONPATH=.. python manage.py makemigrations ui`
Expected: utworzony `0112_customer_priority_rank.py` z `AddField`.

- [ ] **Step 3: Napisz failing test kolejności**

`web/ui/tests/test_call_queue_order.py`:
```python
from datetime import date
from django.contrib.auth.models import Group, User
from django.test import TestCase, RequestFactory
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN
from ui.views.hu_control import _call_queue


class CallQueueOrderTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u = User.objects.create_user("c", "c@e.pl", "Zx9!longpass")
        self.u.groups.add(g)  # admin omija strefy
        self.rf = RequestFactory()

    def _hu(self, name, cdate, status="planned", vip=False, prio=False):
        cust = Customer.objects.create(name=name, is_vip=vip)
        sh = Shipment.objects.create(name=name, customer=cust,
                                     outbound_created_date=cdate)
        return HandlingUnit.objects.create(shipment=sh, code=name,
                                           status=status, is_priority=prio)

    def _order(self):
        req = self.rf.get("/"); req.user = self.u
        return [h.code for h in _call_queue(req)]

    def test_leader_priority_beats_all(self):
        self._hu("VIP", date(2026, 8, 1), vip=True)
        self._hu("PRIO", date(2026, 8, 3), prio=True)
        self.assertEqual(self._order()[0], "PRIO")

    def test_recheck_before_planned(self):
        self._hu("PLAN", date(2026, 8, 1))
        self._hu("RECH", date(2026, 8, 3), status="to_recheck")
        self.assertEqual(self._order()[0], "RECH")

    def test_vip_then_fifo(self):
        self._hu("STD_OLD", date(2026, 8, 1))
        self._hu("VIP_NEW", date(2026, 8, 5), vip=True)
        self.assertEqual(self._order(), ["VIP_NEW", "STD_OLD"])
```

- [ ] **Step 4: Uruchom — ma nie przejść**

Run: `cd web && DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_call_queue_order -v 2`
Expected: ImportError `_call_queue` / FAIL.

- [ ] **Step 5: Zaimplementuj `_call_queue`**

W `web/ui/views/hu_control.py` (obok `_controllable`):
```python
def _call_queue(request):
    """Wspólna kolejka wywołań: przefiltrowana strefą/typem, posortowana wg priorytetów.
    Używana przez listę i `hu_control_next`. Pomija palety zajęte przez innych i snooze."""
    from django.db.models import Q
    from django.utils import timezone
    now = timezone.now()
    qs = _controllable(request, HandlingUnit.objects.select_related(
        "shipment", "shipment__customer")).filter(
        status__in=("planned", "to_recheck"))
    # wolne: bez rezerwacji albo moje; snooze wygasł
    qs = qs.filter(Q(called_at__isnull=True) | Q(assigned_to=request.user))
    qs = qs.filter(Q(snooze_until__isnull=True) | Q(snooze_until__lte=now))
    return qs.order_by(
        "-is_priority",
        "-status",                                   # to_recheck > planned alfabetycznie
        "-shipment__customer__is_vip",
        "-shipment__customer__priority_rank",
        "shipment__outbound_created_date",
        "id",
    )
```
Uwaga: `called_at`/`snooze_until` dodaje Task 2 — ten test nie ustawia rezerwacji, więc filtr `called_at__isnull=True` przepuszcza wszystko. Jeśli pola jeszcze nie ma (kolejność tasków), dodaj je razem w tym tasku migracją `0112` (przenieś definicje pól z Taska 2 tutaj). **Rekomendacja: scal pola `called_at`/`snooze_until` do migracji 0112**, żeby helper działał od razu; Task 2 tylko ich używa.

- [ ] **Step 6: Dodaj `called_at`/`snooze_until` do `HandlingUnit` i migruj**

W `web/ui/models.py` (HandlingUnit, obok `assigned_to`):
```python
    called_at = models.DateTimeField(null=True, blank=True, verbose_name="Wywołana o")
    snooze_until = models.DateTimeField(null=True, blank=True, verbose_name="Odłożona do")
```
Run: `cd web && PYTHONPATH=.. python manage.py makemigrations ui`
(zaktualizuje 0112 lub doda 0113 — obie OK).

- [ ] **Step 7: Uruchom — ma przejść**

Run: (jak Step 4) Expected: OK (3 testy).

- [ ] **Step 8: Commit**
```bash
git add web/ui/models.py web/ui/migrations/011*_*.py web/ui/views/hu_control.py web/ui/tests/test_call_queue_order.py
git commit -m "HU wywołania: helper _call_queue + pola rezerwacji i priority_rank"
```

---

### Task 2: `hu_call` — rezerwacja pod lockiem (pierwszy wygrywa)

**Files:**
- Modify: `web/ui/views/hu_control.py` (nowy widok `hu_call`)
- Modify: `web/ui/urls.py` (URL `hu_call`)
- Test: `web/ui/tests/test_hu_call_lock.py`

**Interfaces:**
- Consumes: `_call_queue`, `_zone_ok`, `_log_status`, `HandlingUnit.called_at/assigned_to`.
- Produces: URL `ui:hu_call` (POST, arg `pk`) — rezerwuje i przekierowuje na `ui:hu_control_detail`; zajętą odrzuca komunikatem.

- [ ] **Step 1: Failing test locka**

`web/ui/tests/test_hu_call_lock.py`:
```python
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN


class HuCallLockTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u1 = User.objects.create_user("a", "a@e.pl", "Zx9!longpass"); self.u1.groups.add(g)
        self.u2 = User.objects.create_user("b", "b@e.pl", "Zx9!longpass"); self.u2.groups.add(g)
        sh = Shipment.objects.create(name="S", customer=Customer.objects.create(name="K"))
        self.hu = HandlingUnit.objects.create(shipment=sh, code="HU1", status="planned")

    def test_first_wins(self):
        self.client.force_login(self.u1)
        r = self.client.post(reverse("ui:hu_call", args=[self.hu.pk]))
        self.assertRedirects(r, reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.assigned_to, self.u1)
        self.assertIsNotNone(self.hu.called_at)

    def test_second_gets_blocked(self):
        self.hu.assigned_to = self.u1
        from django.utils import timezone
        self.hu.called_at = timezone.now(); self.hu.save()
        self.client.force_login(self.u2)
        r = self.client.post(reverse("ui:hu_call", args=[self.hu.pk]))
        self.assertRedirects(r, reverse("ui:hu_control_menu"))
        self.hu.refresh_from_db()
        self.assertEqual(self.hu.assigned_to, self.u1)  # nie przejęte
```

- [ ] **Step 2: Uruchom — FAIL** (brak URL `ui:hu_call`).
Run: `cd web && DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_hu_call_lock -v 2`

- [ ] **Step 3: Widok `hu_call`**

W `web/ui/views/hu_control.py`:
```python
@_controller
@require_POST
def hu_call(request, pk):
    """Wywołanie/rezerwacja palety: atomowo, pierwszy wygrywa."""
    with transaction.atomic():
        hu = HandlingUnit.objects.select_for_update().select_related("shipment").get(pk=pk)
        if not _zone_ok(request.user, hu):
            messages.warning(request, f"Brak uprawnień do strefy „{hu.warehouse_type or '—'}”.")
            return redirect("ui:hu_control_menu")
        if hu.called_at and hu.assigned_to_id and hu.assigned_to_id != request.user.id:
            messages.warning(request, "Ta paleta jest już wywołana przez innego kontrolera.")
            return redirect("ui:hu_control_menu")
        hu.assigned_to = request.user
        hu.called_at = timezone.now()
        hu.snooze_until = None
        hu.save(update_fields=["assigned_to", "called_at", "snooze_until"])
        _log_status(hu, hu.status, hu.status, request.user, "wywołanie (rezerwacja)")
    return redirect("ui:hu_control_detail", pk=pk)
```

- [ ] **Step 4: URL**

W `web/ui/urls.py` (sekcja kontroli HU):
```python
    path("kontrola/hu/<int:pk>/wywolaj/", views.hu_call, name="hu_call"),
```

- [ ] **Step 5: Uruchom — PASS** (jak Step 2).

- [ ] **Step 6: Commit**
```bash
git add web/ui/views/hu_control.py web/ui/urls.py web/ui/tests/test_hu_call_lock.py
git commit -m "HU wywołania: hu_call z rezerwacją pod lockiem (pierwszy wygrywa)"
```

---

### Task 3: `hu_release` — odmowa z powodem / snooze

**Files:**
- Modify: `web/ui/views/hu_control.py` (`hu_release`)
- Modify: `web/ui/urls.py`
- Test: `web/ui/tests/test_hu_release.py`

**Interfaces:**
- Consumes: `HandlingUnit.called_at/snooze_until/assigned_to`, `_log_status`.
- Produces: URL `ui:hu_release` (POST, `pk`, POST-param `reason`, opcjonalnie `snooze_min`).

- [ ] **Step 1: Failing test**

`web/ui/tests/test_hu_release.py`:
```python
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN


class HuReleaseTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u = User.objects.create_user("a", "a@e.pl", "Zx9!longpass"); self.u.groups.add(g)
        sh = Shipment.objects.create(name="S", customer=Customer.objects.create(name="K"))
        self.hu = HandlingUnit.objects.create(shipment=sh, code="HU1", status="planned",
                                              assigned_to=self.u, called_at=timezone.now())
        self.client.force_login(self.u)

    def test_refuse_returns_to_queue(self):
        self.client.post(reverse("ui:hu_release", args=[self.hu.pk]), {"reason": "brak palety"})
        self.hu.refresh_from_db()
        self.assertIsNone(self.hu.assigned_to)
        self.assertIsNone(self.hu.called_at)

    def test_snooze_sets_until(self):
        self.client.post(reverse("ui:hu_release", args=[self.hu.pk]), {"snooze_min": "10"})
        self.hu.refresh_from_db()
        self.assertIsNone(self.hu.called_at)
        self.assertGreater(self.hu.snooze_until, timezone.now())
```

- [ ] **Step 2: Uruchom — FAIL.**
Run: `cd web && DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_hu_release -v 2`

- [ ] **Step 3: Widok**
```python
@_controller
@require_POST
def hu_release(request, pk):
    """Odmowa (z powodem, wraca do kolejki) lub snooze (odłożenie na X minut)."""
    from datetime import timedelta
    hu = get_object_or_404(HandlingUnit, pk=pk)
    if hu.assigned_to_id and hu.assigned_to_id != request.user.id:
        messages.error(request, "To nie Twoja rezerwacja.")
        return redirect("ui:hu_control_menu")
    snooze_min = (request.POST.get("snooze_min") or "").strip()
    hu.assigned_to = None
    hu.called_at = None
    if snooze_min.isdigit() and int(snooze_min) > 0:
        hu.snooze_until = timezone.now() + timedelta(minutes=int(snooze_min))
        note = f"odłożono na {snooze_min} min"
    else:
        hu.snooze_until = None
        note = f"odmowa: {(request.POST.get('reason') or '').strip()[:180]}"
    hu.save(update_fields=["assigned_to", "called_at", "snooze_until"])
    _log_status(hu, hu.status, hu.status, request.user, note)
    return redirect("ui:hu_control_menu")
```

- [ ] **Step 4: URL**
```python
    path("kontrola/hu/<int:pk>/zwolnij/", views.hu_release, name="hu_release"),
```

- [ ] **Step 5: Uruchom — PASS.**

- [ ] **Step 6: Commit**
```bash
git add web/ui/views/hu_control.py web/ui/urls.py web/ui/tests/test_hu_release.py
git commit -m "HU wywołania: hu_release (odmowa z powodem / snooze)"
```

---

### Task 4: `hu_control_next` — auto-przydział z bilansem obciążenia

**Files:**
- Modify: `web/ui/views/hu_control.py` (przebudowa `hu_control_next`)
- Test: `web/ui/tests/test_hu_control_next.py` (rozszerz istniejące, jeśli są)

**Interfaces:**
- Consumes: `_call_queue`.
- Produces: `hu_control_next` bierze czoło `_call_queue`, preferuje przydzielone (`assigned_to=me`), rezerwuje pod lockiem (reuse logiki `hu_call`).

- [ ] **Step 1: Failing test**

`web/ui/tests/test_hu_control_next.py`:
```python
from datetime import date
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN


class HuControlNextTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u = User.objects.create_user("a", "a@e.pl", "Zx9!longpass"); self.u.groups.add(g)
        self.client.force_login(self.u)

    def _hu(self, name, cdate, vip=False):
        c = Customer.objects.create(name=name, is_vip=vip)
        sh = Shipment.objects.create(name=name, customer=c, outbound_created_date=cdate)
        return HandlingUnit.objects.create(shipment=sh, code=name, status="planned")

    def test_next_picks_queue_head_and_reserves(self):
        old = self._hu("OLD", date(2026, 8, 1))
        self._hu("VIP", date(2026, 8, 9), vip=True)  # VIP bije FIFO
        r = self.client.get(reverse("ui:hu_control_next"))
        vip = HandlingUnit.objects.get(code="VIP")
        self.assertRedirects(r, reverse("ui:hu_control_detail", args=[vip.pk]))
        vip.refresh_from_db()
        self.assertEqual(vip.assigned_to, self.u)  # zarezerwowane
```

- [ ] **Step 2: Uruchom — FAIL** (obecny `hu_control_next` nie rezerwuje / inna kolejność).

- [ ] **Step 3: Przebuduj `hu_control_next`**

Zastąp ciało `hu_control_next`:
```python
@_controller
def hu_control_next(request):
    """Auto-przydział: przydzielone mi najpierw, potem czoło wspólnej kolejki (_call_queue).
    Rezerwuje wybraną paletę pod lockiem (pierwszy wygrywa)."""
    q = _call_queue(request)
    hu = q.filter(assigned_to=request.user).first() or q.first()
    if not hu:
        messages.info(request, "Brak HU do kontroli w Twojej strefie.")
        return redirect("ui:hu_control_menu")
    with transaction.atomic():
        locked = HandlingUnit.objects.select_for_update().get(pk=hu.pk)
        if locked.called_at and locked.assigned_to_id not in (None, request.user.id):
            return redirect("ui:hu_control_next")  # ktoś nas ubiegł → następna
        locked.assigned_to = request.user
        locked.called_at = timezone.now()
        locked.save(update_fields=["assigned_to", "called_at"])
        _log_status(locked, locked.status, locked.status, request.user, "wywołanie (następna)")
    return redirect("ui:hu_control_detail", pk=hu.pk)
```

- [ ] **Step 4: Uruchom — PASS.** Uruchom też istniejące testy HU control, by nie było regresji:
`... python manage.py test ui.tests.test_hu_control -v 1`

- [ ] **Step 5: Commit**
```bash
git add web/ui/views/hu_control.py web/ui/tests/test_hu_control_next.py
git commit -m "HU wywołania: hu_control_next = auto-przydział z kolejki + rezerwacja"
```

---

### Task 5: Niekompletność przesyłki z feedu SAP + baner

**Files:**
- Modify: `web/ui/models.py` (Shipment — pola + helper `picking_summary`)
- Modify: `web/ui/views/hu.py` (import feedu — mapowanie `picking_complete`)
- Modify: `web/ui/templates/ui/scanner/hu_detail.html` (baner)
- Modify: `web/ui/views/hu_control.py` (`hu_control_detail` — przekaż flagę)
- Test: `web/ui/tests/test_incomplete_shipment.py`

**Interfaces:**
- Produces: `Shipment.picking_complete: bool`, `Shipment.picking_summary() -> dict(ready:int, waiting:bool)`.

- [ ] **Step 1: Pola na Shipment**
```python
    picking_complete = models.BooleanField(default=False, db_index=True,
                                            verbose_name="Picking zakończony")
    picking_complete_at = models.DateTimeField(null=True, blank=True)
    picking_eta_note = models.CharField(max_length=200, blank=True,
                                        verbose_name="Status pickingu (ETA)")
    picking_eta_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True,
                                       blank=True, related_name="picking_eta_updates")
    picking_eta_at = models.DateTimeField(null=True, blank=True)
```
oraz metoda:
```python
    def picking_summary(self):
        """Ile palet gotowych (is_completed) i czy czekamy na kolejne (picking niezakończony)."""
        ready = sum(1 for h in self.handling_units.all() if h.is_completed)
        return {"ready": ready, "waiting": not self.picking_complete}
```

- [ ] **Step 2: Migracja** — `makemigrations ui`.

- [ ] **Step 3: Failing test**

`web/ui/tests/test_incomplete_shipment.py`:
```python
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN


class IncompleteShipmentTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u = User.objects.create_user("a", "a@e.pl", "Zx9!longpass"); self.u.groups.add(g)
        self.client.force_login(self.u)
        self.sh = Shipment.objects.create(name="S", customer=Customer.objects.create(name="K"),
                                          picking_complete=False)
        HandlingUnit.objects.create(shipment=self.sh, code="H1", is_completed=True)
        self.hu = HandlingUnit.objects.create(shipment=self.sh, code="H2", is_completed=True,
                                              status="planned")

    def test_summary_counts_ready_and_waiting(self):
        s = self.sh.picking_summary()
        self.assertEqual(s["ready"], 2)
        self.assertTrue(s["waiting"])

    def test_detail_shows_incomplete_banner(self):
        r = self.client.get(reverse("ui:hu_control_detail", args=[self.hu.pk]))
        self.assertContains(r, "NIEKOMPLETN")
```

- [ ] **Step 4: Uruchom — FAIL.**

- [ ] **Step 5: Przekaż flagę w `hu_control_detail`** — dodaj do słownika kontekstu `render(... "ui/scanner/hu_detail.html", {...})`:
```python
        "shipment_incomplete": bool(hu.shipment_id and not hu.shipment.picking_complete),
```

- [ ] **Step 6: Baner w `hu_detail.html`** — na górze karty:
```html
{% if shipment_incomplete %}
  <div class="banner banner--warn">⚠ Sprawdzasz NIEKOMPLETNĄ przesyłkę do klienta — picking jeszcze trwa.</div>
{% endif %}
```

- [ ] **Step 7: Mapowanie feedu** w `web/ui/views/hu.py` (import HU) — tam gdzie tworzony/aktualizowany jest `Shipment`, ustaw z kolumny feedu (nazwa kolumny wg realnego feedu, np. `PICKING_DONE`):
```python
        sh.picking_complete = _truthy(row.get("PICKING_DONE"))
        if sh.picking_complete and not sh.picking_complete_at:
            sh.picking_complete_at = timezone.now()
```
(reuse istniejącego helpera parsującego truthy; jeśli brak, dodaj `_truthy(v): return str(v).strip().lower() in ("1","true","tak","x","yes")`).

- [ ] **Step 8: Uruchom — PASS.**

- [ ] **Step 9: Commit**
```bash
git add web/ui/models.py web/ui/migrations/*.py web/ui/views/hu.py web/ui/views/hu_control.py web/ui/templates/ui/scanner/hu_detail.html web/ui/tests/test_incomplete_shipment.py
git commit -m "HU wywołania: niekompletność przesyłki z feedu SAP + baner kontroli"
```

---

### Task 6: Lista — grupowanie po odbiorcy, akcje wiersza, wywołanie wsadowe

**Files:**
- Modify: `web/ui/views/hu_control.py` (`planner_stock_contents` hu-mode; `hu_call_batch`)
- Modify: `web/ui/urls.py`
- Modify: `web/ui/templates/ui/stock_contents.html`
- Test: `web/ui/tests/test_hu_list_view.py` (rozszerz istniejące)

**Interfaces:**
- Consumes: `_call_queue`, `Shipment.picking_summary`.
- Produces: grupa `recipient` w `_HU_GROUPS`; URL `ui:hu_call_batch` (POST, param `shipment_id` → wywołaj gotowe).

- [ ] **Step 1: Failing test** (dopisz do `test_hu_list_view.py`):
```python
    def test_group_by_recipient(self):
        r = self.client.get(self.url, {"view": "hu", "group": "recipient"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("group_rows", r.context)

    def test_batch_call_ready_for_recipient(self):
        from django.urls import reverse
        sh_id = self.h_new.shipment_id
        self.client.post(reverse("ui:hu_call_batch"), {"shipment_id": sh_id})
        self.h_new.refresh_from_db()
        self.assertIsNotNone(self.h_new.called_at)  # gotowa (is_completed) → wywołana
```

- [ ] **Step 2: Uruchom — FAIL.**

- [ ] **Step 3: Dodaj grupę `recipient`** do słownika `_HU_GROUPS` w `hu_control.py`:
```python
    "recipient": ("shipment__recipient_name", "recipient"),
```
(jeśli `recipient_name` bywa puste, fallback na `shipment__customer__name` — użyj `shipment__customer__name` gdy tak wygodniej).

- [ ] **Step 4: Widok wsadowy**
```python
@_controller
@require_POST
def hu_call_batch(request):
    """Wywołaj wszystkie GOTOWE (is_completed) palety danej przesyłki dla bieżącego kontrolera."""
    sid = request.POST.get("shipment_id")
    n = 0
    for hu in _call_queue(request).filter(shipment_id=sid, is_completed=True):
        with transaction.atomic():
            locked = HandlingUnit.objects.select_for_update().get(pk=hu.pk)
            if locked.called_at and locked.assigned_to_id not in (None, request.user.id):
                continue
            locked.assigned_to = request.user
            locked.called_at = timezone.now()
            locked.save(update_fields=["assigned_to", "called_at"])
            _log_status(locked, locked.status, locked.status, request.user, "wywołanie wsadowe")
            n += 1
    messages.success(request, f"Wywołano {n} palet.")
    return redirect(request.META.get("HTTP_REFERER", "ui:planner_stock_contents"))
```

- [ ] **Step 5: URL**
```python
    path("kontrola/hu/wywolaj-wsad/", views.hu_call_batch, name="hu_call_batch"),
```

- [ ] **Step 6: Szablon** — w `stock_contents.html`, w trybie hu, przy wierszu palety dodaj przycisk „Wywołaj":
```html
<form method="post" action="{% url 'ui:hu_call' h.pk %}" style="display:inline">{% csrf_token %}
  <button class="btn btn--sm btn--ok" type="submit">Wywołaj</button>
</form>
```
a w nagłówku grupy odbiorcy przycisk wsadowy + badge niekompletności:
```html
{% with s=group_shipment.picking_summary %}
  {% if s.waiting %}<span class="badge badge--warn">NIEKOMPLETNA · {{ s.ready }} gotowych</span>{% endif %}
{% endwith %}
<form method="post" action="{% url 'ui:hu_call_batch' %}" style="display:inline">{% csrf_token %}
  <input type="hidden" name="shipment_id" value="{{ group_shipment.id }}">
  <button class="btn btn--sm" type="submit">Wywołaj gotowe</button>
</form>
```
(Przekaż `group_shipment` w kontekście grupy, gdy `group == "recipient"`.)

- [ ] **Step 7: Uruchom — PASS** (`test_hu_list_view`).

- [ ] **Step 8: Commit**
```bash
git add web/ui/views/hu_control.py web/ui/urls.py web/ui/templates/ui/stock_contents.html web/ui/tests/test_hu_list_view.py
git commit -m "HU wywołania: lista po odbiorcy + akcje wiersza + wywołanie wsadowe"
```

---

### Task 7: `EscalationRoute` + `hu_escalate` + status zwrotny ETA

**Files:**
- Modify: `web/ui/models.py` (`EscalationRoute`)
- Modify: `web/ui/views/hu_control.py` (`hu_escalate`, `picking_eta`)
- Modify: `web/ui/urls.py`
- Test: `web/ui/tests/test_escalation.py`

**Interfaces:**
- Consumes: silnik `Task` (`Task.objects.get_or_create(dedup_key=...)`), `Shipment.picking_eta_note`.
- Produces: `EscalationRoute.resolve(warehouse_type) -> EscalationRoute|None`; URL `ui:hu_escalate` (POST, `shipment_id`), `ui:picking_eta` (POST, `shipment_id`, `note`).

- [ ] **Step 1: Model**
```python
class EscalationRoute(models.Model):
    """Mapa eskalacji niekompletnej przesyłki: kto (picking/obszar/zmiana) dla danego typu magazynu.
    Pusty warehouse_type = reguła globalna (fallback)."""
    warehouse_type = models.CharField(max_length=40, blank=True, db_index=True,
                                      verbose_name="Typ magazynu (puste = globalna)")
    picking_leader = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="esc_picking")
    area_leader = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="esc_area")
    shift_manager = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="esc_shift")
    escalate_after_minutes = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "Ścieżka eskalacji"
        verbose_name_plural = "Ścieżki eskalacji"

    @classmethod
    def resolve(cls, warehouse_type):
        return (cls.objects.filter(warehouse_type=warehouse_type or "").first()
                or cls.objects.filter(warehouse_type="").first())
```

- [ ] **Step 2: Migracja** — `makemigrations ui`.

- [ ] **Step 3: Failing test**

`web/ui/tests/test_escalation.py`:
```python
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from ui.models import Customer, EscalationRoute, HandlingUnit, Shipment, Task
from ui.roles import GROUP_ADMIN


class EscalationTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u = User.objects.create_user("a", "a@e.pl", "Zx9!longpass"); self.u.groups.add(g)
        self.lead = User.objects.create_user("pl", "pl@e.pl", "Zx9!longpass")
        self.client.force_login(self.u)
        EscalationRoute.objects.create(warehouse_type="", picking_leader=self.lead)
        self.sh = Shipment.objects.create(name="S", customer=Customer.objects.create(name="K"),
                                          picking_complete=False)
        HandlingUnit.objects.create(shipment=self.sh, code="H1", warehouse_type="P1", status="planned")

    def test_escalate_creates_task(self):
        self.client.post(reverse("ui:hu_escalate"), {"shipment_id": self.sh.pk})
        self.assertTrue(Task.objects.filter(dedup_key=f"picking_incomplete:{self.sh.pk}").exists())

    def test_eta_note_saved(self):
        self.client.post(reverse("ui:picking_eta"),
                         {"shipment_id": self.sh.pk, "note": "paleta 7 o 14:30"})
        self.sh.refresh_from_db()
        self.assertEqual(self.sh.picking_eta_note, "paleta 7 o 14:30")
```

- [ ] **Step 4: Uruchom — FAIL.**

- [ ] **Step 5: Widoki**
```python
@_controller
@require_POST
def hu_escalate(request):
    """Eskaluj niekompletną przesyłkę do liderów wg EscalationRoute (Task per adresat)."""
    sh = get_object_or_404(Shipment, pk=request.POST.get("shipment_id"))
    wt = sh.handling_units.exclude(warehouse_type="").values_list("warehouse_type", flat=True).first() or ""
    route = EscalationRoute.resolve(wt)
    targets = [t for t in (getattr(route, "picking_leader", None),
                           getattr(route, "area_leader", None),
                           getattr(route, "shift_manager", None)) if t]
    Task.objects.get_or_create(
        dedup_key=f"picking_incomplete:{sh.pk}"[:120],
        defaults=dict(title=f"Niekompletna przesyłka: {sh.name}",
                      description=f"Kontrola zgłasza niekompletność przesyłki {sh.name} "
                                  f"({sh.recipient_name or sh.customer}). Uzupełnij picking.",
                      assigned_to=targets[0] if targets else None))
    messages.success(request, "Zgłoszono niekompletność do liderów.")
    return redirect(request.META.get("HTTP_REFERER", "ui:planner_stock_contents"))


@_controller
@require_POST
def picking_eta(request):
    """Status zwrotny lidera pickingu — ETA brakujących palet (widoczny u kontrolera)."""
    sh = get_object_or_404(Shipment, pk=request.POST.get("shipment_id"))
    sh.picking_eta_note = (request.POST.get("note") or "").strip()[:200]
    sh.picking_eta_by = request.user
    sh.picking_eta_at = timezone.now()
    sh.save(update_fields=["picking_eta_note", "picking_eta_by", "picking_eta_at"])
    return redirect(request.META.get("HTTP_REFERER", "ui:planner_stock_contents"))
```
(Dopasuj pola `Task` do realnego modelu — sprawdź `Task` w `models.py`; użyj istniejących `title`/`description`/`assigned_to`/`dedup_key`. Jeśli `Task` nie ma `assigned_to`, pomiń.)

- [ ] **Step 6: URL-e**
```python
    path("kontrola/hu/eskaluj/", views.hu_escalate, name="hu_escalate"),
    path("kontrola/hu/picking-eta/", views.picking_eta, name="picking_eta"),
```

- [ ] **Step 7: Uruchom — PASS.**

- [ ] **Step 8: Commit**
```bash
git add web/ui/models.py web/ui/migrations/*.py web/ui/views/hu_control.py web/ui/urls.py web/ui/tests/test_escalation.py
git commit -m "HU wywołania: EscalationRoute + hu_escalate + status zwrotny ETA"
```

---

### Task 8: Audyt wywołań — `HUStatusEvent.kind` + metryki

**Files:**
- Modify: `web/ui/models.py` (`HUStatusEvent.kind`; `_log_status`/log call rozróżnia kind)
- Modify: `web/ui/views/hu_control.py` (log wywołań z `kind="call"`, zwolnień `kind="release"`)
- Test: `web/ui/tests/test_call_metrics.py`

**Interfaces:**
- Produces: `HUStatusEvent.kind ∈ {status, call, release}`; helper `_log_call(hu, user, note, kind)`.

- [ ] **Step 1: Pole + helper**

W `models.py` (HUStatusEvent):
```python
    KIND = [("status", "Zmiana statusu"), ("call", "Wywołanie"), ("release", "Zwolnienie")]
    kind = models.CharField(max_length=8, choices=KIND, default="status", db_index=True)
```
Migracja: `makemigrations ui`.

W `hu_control.py` dodaj helper i użyj go w `hu_call`/`hu_release`/`hu_control_next`/`hu_call_batch` zamiast `_log_status(..., note)`:
```python
def _log_call(hu, user, note, kind="call"):
    HUStatusEvent.objects.create(hu=hu, from_status=hu.status, to_status=hu.status,
                                 by_user=user, note=note[:200], kind=kind)
```
(Podmień wcześniejsze `_log_status(hu, hu.status, hu.status, user, "wywołanie...")` na `_log_call(hu, user, "...", "call")`; dla `hu_release` → `kind="release"`.)

- [ ] **Step 2: Failing test**

`web/ui/tests/test_call_metrics.py`:
```python
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from ui.models import Customer, HUStatusEvent, HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN


class CallMetricsTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u = User.objects.create_user("a", "a@e.pl", "Zx9!longpass"); self.u.groups.add(g)
        self.client.force_login(self.u)
        sh = Shipment.objects.create(name="S", customer=Customer.objects.create(name="K"))
        self.hu = HandlingUnit.objects.create(shipment=sh, code="H1", status="planned")

    def test_call_logs_kind_call(self):
        self.client.post(reverse("ui:hu_call", args=[self.hu.pk]))
        self.assertTrue(HUStatusEvent.objects.filter(hu=self.hu, kind="call").exists())
```

- [ ] **Step 3: Uruchom — FAIL → zaimplementuj (Step 1 podmiany) → PASS.**

- [ ] **Step 4: Commit**
```bash
git add web/ui/models.py web/ui/migrations/*.py web/ui/views/hu_control.py web/ui/tests/test_call_metrics.py
git commit -m "HU wywołania: audyt HUStatusEvent.kind (call/release) + metryki"
```

---

### Task 9: Wydajność listy — N+1 + hu_metrics, pin liczby zapytań

**Files:**
- Modify: `web/ui/views/hu_control.py` (`planner_stock_contents` hu-mode — select_related/prefetch)
- Test: `web/ui/tests/test_hu_list_perf.py`

**Interfaces:** brak nowych — optymalizacja zapytań.

- [ ] **Step 1: Failing test (pin zapytań)**

`web/ui/tests/test_hu_list_perf.py`:
```python
from datetime import date
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from ui.models import Customer, HandlingUnit, Shipment
from ui.roles import GROUP_ADMIN


class HuListPerfTests(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name=GROUP_ADMIN)
        self.u = User.objects.create_user("a", "a@e.pl", "Zx9!longpass"); self.u.groups.add(g)
        self.client.force_login(self.u)
        for i in range(30):
            c = Customer.objects.create(name=f"K{i}", is_vip=(i % 2 == 0))
            sh = Shipment.objects.create(name=f"S{i}", customer=c,
                                         outbound_created_date=date(2026, 8, 1))
            HandlingUnit.objects.create(shipment=sh, code=f"H{i}", status="planned")

    def test_list_query_count_bounded(self):
        with self.assertNumQueries(FewerThan := 15):  # stała, niezależna od liczby wierszy
            self.client.get(reverse("ui:planner_stock_contents"), {"view": "hu"})
```
(Dostrój próg do realnego baseline po implementacji: najpierw zmierz, wpisz baseline+0.)

- [ ] **Step 2: Uruchom — zmierz** (może FAIL na N+1).

- [ ] **Step 3: Zoptymalizuj hu-mode** w `planner_stock_contents`:
   - queryset listy: `.select_related("shipment", "shipment__customer").prefetch_related("items")`;
   - `hu_metrics(page_obj.object_list)` wołaj RAZ na stronie (jest); upewnij się, że nie robi zapytań per HU — jeśli robi, dołóż `prefetch_related` potrzebnych relacji w queryście strony.

- [ ] **Step 4: Uruchom — PASS** (ustaw próg na zmierzony baseline).

- [ ] **Step 5: Pełen zestaw kontroli HU (regresja)**

Run: `cd web && DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' PYTHONPATH=.. python manage.py test ui.tests.test_hu_control ui.tests.test_hu_list_view ui.tests.test_call_queue_order ui.tests.test_hu_call_lock -v 1`
Expected: OK.

- [ ] **Step 6: Commit**
```bash
git add web/ui/views/hu_control.py web/ui/tests/test_hu_list_perf.py
git commit -m "HU wywołania: wydajność listy (N+1/hu_metrics) + pin liczby zapytań"
```

---

## Self-review (pokrycie spec → task)

- Kolejka/priorytety → Task 1,4 ✓
- Wywołanie+lock (pierwszy wygrywa) → Task 2,4 ✓
- Odmowa/snooze → Task 3 ✓
- Blokady strefa/typ/zajęte → Task 1 (`_controllable`/`_zone_ok`) + 2 ✓
- Grupowanie po odbiorcy + akcje + wsadowo → Task 6 ✓
- Niekompletność (feed SAP, licznik, badge, baner) → Task 5,6 ✓
- Eskalacja + status zwrotny ETA → Task 7 ✓
- Master data (VIP/priority_rank, EscalationRoute) → Task 1,7 ✓ (mapa strefa→lider = EscalationRoute)
- Audyt (kind, czas wywołanie→start, porzucone) → Task 8 ✓
- Wydajność (N+1, hu_metrics) → Task 9 ✓
- Tylko online → brak zmian offline (świadomie) ✓
