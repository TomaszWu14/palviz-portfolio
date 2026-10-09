# Redesign modułu wywoływania palet do kontroli HU

Data: 2026-08-05
Status: zaakceptowany kierunek (podejście A), do przeglądu
Moduł: Kontrola HU (`web/ui/views/hu_control.py`, `templates/ui/scanner/`, `templates/ui/stock_contents.html`)

## Cel

Zamienić dzisiejszą **read-only listę HU** + pojedynczy `hu_control_next` w **interaktywną kolejkę
wywoływania palet z auto-przydziałem**, świadomą kompletacji na poziomie przesyłki/odbiorcy, z
eskalacją niekompletności i lekkim audytem — bez fizycznego zlecania dowozu (wywołanie jest cyfrowe).

Zakres: pełny redesign (funkcjonalność + wydajność + audyt).

## Kluczowe fakty domenowe

- Kompletność jest na poziomie **przesyłki/odbiorcy, nie palety.** Odbiorca może mieć np. 7 palet;
  gdy gotowych jest 6, kontroler *może* je sprawdzać, ale musi **wiedzieć**, że przesyłka jest
  niekompletna. **Finalnej liczby palet nikt nie zna z góry.**
- Priorytet wynika przede wszystkim z **master daty (VIP odbiorcy)**, automatycznie; lider może dobić
  ręcznie (`is_priority`) jako override.
- Wywołanie = otwarcie kontroli + **rezerwacja (lock, pierwszy wygrywa)** + zmiana statusu.
- Tylko online (lock wymaga serwera).

## Model danych (minimalne rozszerzenia — reuse istniejącego)

### `HandlingUnit` (nowe pola)
- `called_at: DateTimeField(null)` — moment wywołania/rezerwacji (przed `in_control`).
- `snooze_until: DateTimeField(null)` — odłożenie palety; kolejka pomija ją do tego czasu.
- Rezerwacja = `assigned_to = user` + `called_at = now`, przy `status="planned"`/`to_recheck`.
  `in_control` nadal ustawia dopiero pierwsze liczenie (`_ensure_started`) lub jawny start.
- Lock: `select_for_update()` + sprawdzenie, że `called_at`/`assigned_to` wolne lub moje.

### `Customer` (master data — nowe)
- Reuse `is_vip` jako główny sygnał priorytetu.
- `priority_rank: PositiveSmallIntegerField(default=0)` — opcjonalny tie-break (wyższy = wyżej), gdy
  sam VIP nie wystarcza.

### `Shipment` (kompletacja przesyłki — nowe)
- `picking_complete: BooleanField(default=False, db_index=True)` — ustawiane **z feedu SAP** przy
  imporcie (nowa kolumna feedu), gdy picking uzna przesyłkę za skompletowaną. Dopóki `False` →
  „X gotowych, czekamy". Bez ręcznego przełącznika lidera (źródłem prawdy jest feed).
- `picking_complete_at: DateTimeField(null)`.
- `picking_eta_note: CharField(max_length=200, blank)` — **status zwrotny** od lidera pickingu
  („paleta 7 w toku / ETA 14:30"), widoczny u kontrolera.
- `picking_eta_by: FK(User, null)` / `picking_eta_at: DateTimeField(null)`.
- Liczniki gotowych/oczekujących liczone z `handling_units` (`is_completed`), NIE z pola docelowego.

### `EscalationRoute` (nowy, mały model — mapa eskalacji)
Reuse wzorca `ControlledWarehouseType`/`ControllerZone`.
- `warehouse_type: CharField(blank)` — pusty = reguła globalna (fallback).
- `picking_leader: FK(User, null)`.
- `area_leader: FK(User, null)`.
- `shift_manager: FK(User, null)`.
- `escalate_after_minutes: PositiveSmallIntegerField(default=0)` — 0 = od razu; >0 = kolejny szczebel
  po czasie bez reakcji.
Rozwiązywanie: dokładny `warehouse_type` → fallback globalny.

### `HUStatusEvent` (audyt — rozszerzenie)
- `kind: CharField(choices=[("status","zmiana statusu"),("call","wywołanie"),("release","zwolnienie")], default="status")`.
- Wywołanie loguje `kind="call"` (`from_status==to_status`); odmowa/snooze → `kind="release"` z powodem
  w `note`. Metryki liczone z tych zdarzeń.

Eskalacja niekompletności korzysta z istniejącego silnika **`Task`** (`dedup_key=f"picking_incomplete:{shipment_id}"`),
po jednym Tasku na adresata z `EscalationRoute` — bez osobnej tabeli.

## Logika kolejki

Jeden współdzielony helper `_call_queue(request)` (zastępuje ad-hoc sortowanie w `hu_control_next`),
używany przez listę i „następną":

```
base = _controllable(request, HandlingUnit.objects.select_related(
        "shipment", "shipment__customer"))
    .filter(status__in=("to_recheck", "planned"))
    .filter(<wolne: called_at IS NULL OR assigned_to == me>)
annotacje:
    a_vip        = shipment.customer.is_vip
    a_rank       = shipment.customer.priority_rank
    a_shortdated = <subquery: min(expiry) pozycji < próg>   # zamiast pętli w Pythonie
order_by:
    -is_priority,                 # 1. ręczny override lidera
    -(status == "to_recheck"),    # 2. rekontrola
    -a_vip, -a_rank,              # 3. VIP / ranga odbiorcy (master data)
    -a_shortdated,                # 4. krótki termin ważności
    shipment.outbound_created_date ASC,  # 5. FIFO (najstarsza dostawa)
    id
```

Strefa/typ egzekwowane przez `_controllable` + `_zone_ok` + `ControlledWarehouseType`.

**Auto-przydział** (`hu_control_next`, tryb domyślny): bierze czoło kolejki dla najmniej obciążonego
kontrolera; **bilans obciążenia** = liczba aktywnych (`in_control`/wywołanych) HU per kontroler w strefie.
Ręczne wejścia (lista, skan, panel lidera) omijają auto-wybór, ale przechodzą przez ten sam lock.

## Wywołanie, odmowa, konflikt

- `hu_call(pk)` **POST**: `transaction.atomic` + `select_for_update`; jeśli wolna → rezerwuj
  (`assigned_to=me`, `called_at=now`, log `kind="call"`), redirect na `hu_control_detail`.
  Jeśli zajęta → komunikat + następna z kolejki (**pierwszy wygrywa**).
- `hu_release(pk)` **POST**: odmowa z powodem (wraca do kolejki, `assigned_to=NULL`, `called_at=NULL`,
  log `kind="release"`) lub **snooze** (`called_at=NULL` + `snooze_until` — wraca po czasie / na koniec).
  → wymaga pola `HandlingUnit.snooze_until: DateTimeField(null)` (filtr kolejki pomija snooze do czasu).
- Takeover istniejącej `in_control` u innego — jak dziś, z logiem (bez zmian).

## Widok listy (hu-mode `planner_stock_contents`)

- **Grupowanie po odbiorcy/przesyłce** (główne) + istniejące dostawa/WZ, strefa, status kompletacji.
- Nagłówek grupy odbiorcy: `„6 gotowych, czekamy"` gdy `shipment.picking_complete == False`;
  **badge „NIEKOMPLETNA"**; gdy `picking_eta_note` → pokaż status zwrotny + kto/kiedy.
- Akcje w wierszu: **Wywołaj/Weź** (`hu_call`), **Eskaluj** (`hu_escalate` — tylko na niekompletnej),
  **Priorytet** (lider), **Podgląd/Etykieta**.
- Wsadowo: **„Wywołaj wszystkie gotowe" per odbiorca** + checkboxy „zaznacz wiele → wywołaj/przydziel".
- Baner przy `hu_control_detail`, gdy `not hu.shipment.picking_complete`:
  „⚠ Sprawdzasz NIEKOMPLETNĄ przesyłkę do klienta".

## Eskalacja

- `hu_escalate(shipment_id)` **POST**: rozwiąż `EscalationRoute` dla `warehouse_type`; utwórz `Task`
  dla `picking_leader` + `area_leader` (+ `shift_manager` wg `escalate_after_minutes`), z linkiem do
  przesyłki. Dedup po `dedup_key`.
- Lider pickingu odpowiada `picking_eta_note` (mały formularz na przesyłce / z Taska) → widoczne u
  kontrolera na badge'u niekompletności.

## Wydajność

- Lista: `select_related("shipment","shipment__customer")` + `prefetch_related("items")`;
  `a_shortdated` jako **subquery/annotate** zamiast pętli w Pythonie.
- `hu_metrics` per strona: policz raz na stronę (już batch) — **cache** wyniku per (strona, filtr) lub
  przenieś ciężkie liczenie do annotacji. Cel: brak N+1, stała liczba zapytań per strona.
- Testy pinują liczbę zapytań (`assertNumQueries`) na liście i „następnej".

## Audyt / metryki

Z `HUStatusEvent` (`kind`): kto/kiedy wywołał; **czas wywołanie→start** (`control_started_at - called_at`);
**porzucone wywołania** (`kind="release"` z powodem); **czas oczekiwania na niekompletne**
(`picking_complete_at - min(called_at)`). Panel lidera pokazuje kolejkę, kto co trzyma i te metryki.

## Testy (Django `ui.tests`)

- `test_call_queue_order.py` — precedencja: priorytet lidera > rekontrola > VIP > short-dated > FIFO;
  strefa/typ filtrują.
- `test_hu_call_lock.py` — pierwszy wygrywa (dwóch woła tę samą → drugi dostaje następną); odmowa i
  snooze wracają do kolejki.
- `test_incomplete_shipment.py` — liczniki „X gotowych/czekamy", badge, baner przy kontroli, brak
  twardej blokady zamknięcia.
- `test_escalation.py` — `hu_escalate` tworzy Taski wg `EscalationRoute` (dedup); `picking_eta_note`
  wraca na badge.
- `test_call_metrics.py` — zdarzenia `call`/`release`, czas wywołanie→start.
- Regresja wydajności — `assertNumQueries` na liście i `hu_control_next`.

## Migracje

Jedna migracja dodająca pola (`HandlingUnit.called_at`/`snooze_until`, `Customer.priority_rank`,
`Shipment.picking_complete`/`_at`/`picking_eta_*`, `HUStatusEvent.kind`) + `EscalationRoute` +
seed pustej reguły globalnej `EscalationRoute` (opcjonalnie w `create_roles`/data-migration).

## Poza zakresem (świadomie)

- Fizyczne zlecenie dowozu palety do strefy (odrzucone w pyt. 4).
- Twarda blokada zamknięcia niekompletnej przesyłki (pyt. 10 — tylko świadomość).
- Offline dla wywołania (pyt. 18 — tylko online).
- Monitoring czasu / auto-alert „za długo" (pyt. 15 — nie wybrane; można w kolejnej iteracji).
