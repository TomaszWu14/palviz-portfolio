# Moduł „Optymalizacja kartonów" (`carton_opt`)

Stan na 2026-08-24 (po PR #514/#517/#518 — hierarchia 3D i kolumny A|B|C).

## Po co jest

Centrum zarządzania **wypełnieniem palet**: wykrywa materiały, których karton źle
wykorzystuje paletę, pozwala eksperymentować z wymiarami opakowań (bez ruszania
master daty), a gdy wariant się sprawdzi — jednym przyciskiem tworzy **nową wersję
instrukcji paletyzacji**. Dostęp: rola **Optymalizacja kartonów** (`GROUP_OPTIMIZER`,
`roles.py`); moduł pod `/optymalizacja/`. Kod: `web/ui/views/carton_opt.py`,
szablony `web/ui/templates/ui/carton_opt/`.

## Cztery zakładki

### 1. Skrzynka zgłoszeń (`/optymalizacja/`)

- Wpadają tu zgłoszenia ze skanera PHV (`PackagingIssue`, typy z
  `OPTIMIZATION_ISSUE_TYPES` w `views/phv.py`): **niedopełniony karton / dopasować
  do palety / za ciężki**; sortowanie wg pilności typu.
- **„Przyjmij zgłoszenie"** → przypisanie operatora + status „w przeglądzie" +
  przekierowanie do warsztatu Wariantów z banerem zgłoszenia (`?issue=<id>`).
- Zgłoszenie można rozwiązać automatycznie przy promocji wariantu
  („Przenieś do instrukcji + rozwiąż zgł.").

### 2. Pilność wypełnienia (dashboard)

- Ranking aktywnych instrukcji z wypełnieniem **poniżej progu**
  (`OptimizationConfig.min_fill_pct`, domyślnie 70%; admin edytuje inline).
- Wypełnienie = objętość kartonów / dostępna objętość ładunku (`_vol_fill`) —
  celowo NIE `cube_used_pct`, bo tamta metryka nie widzi niedopełnienia w pionie.
- 3 poziomy pilności (krytyczna <50% / wysoka / średnia), deep-link do Wariantów.

### 3. Warianty kartonów — główny warsztat

- Wyszukiwarka materiału (REF/EAN) → **trzy kolumny obok siebie: A | B | C**
  (swimlane'y), strona przewijana w dół. Partial: `_variant_column.html`.
- **Wariant A** = stan obecny z instrukcji, nietykalny. **B / C** = zapisywane
  sloty eksperymentów (`CartonAlternative.slot`, historia przez simple_history;
  zapis slotu dezaktywuje poprzedni wariant tego slotu).
- Każda kolumna: **4 poziomy hierarchii od najmniejszego** — Sztuka/opakowanie →
  OPZ → Karton → Paleta — każdy z renderem 3D (paleta 480 px; realistyczna
  geometria EPAL w `palviz-three.js`) i tabelką: wymiary, „mieści N szt. niższego
  poziomu", wypełnienie %.
- **Geometryczna kaskada** (`_hierarchy_levels` + `pack_into`): w B/C edytujesz
  wymiary sztuki, OPZ i kartonu; przeliczenie idzie w górę: szt/OPZ → OPZ/karton →
  kart/paletę. `pack_into` to bezwymiarowa nakładka na `PalletCalculator`
  (kontener = „paleta" o footprincie L×W i max wysokości H, rotacja włączona).
- **Paleta liczona do realnych 220 cm z paletą** (`_OPT_PALLET_MAX_H_CM`) —
  maxy w master dacie bywają niższe (limit klienta/transportu) i są pokazywane
  obok w nawiasie; wiersz „Wysokość stosu X/220 (zapas …)" tłumaczy % wypełnienia.
  Uwaga: tabela wariantów niżej oraz dashboard liczą wg maxu **z instrukcji**.
- Dalej na ekranie: tabela wariantów (fill, Δpp, KPI „−N palet/rok · −M aut/rok"
  wg wolumenu rocznego, `_year_kpi`, 33 palety/auto), **auto-sugestia wymiarów**
  („Zaproponuj wymiary" — `_suggest_dims`, top-5 kartonów w paśmie objętości ±%
  z konfiguracji), **historia promocji**.
- **Domknięcie pętli**: „Przenieś do instrukcji" → walidacja silnikiem → **nowa
  wersja** `PalletizationInstruction` (stara nietknięta, reuse
  `_recalculate_instruction`) + wpis audytowy `CartonPromotion` (kto, kiedy,
  fill przed→po, powiązane zgłoszenie). *Promocja przenosi dziś wymiary kartonu;
  zapis zmian sztuki/OPZ do master-danych = zaplanowany Etap 2
  (spec: `docs/archive/superpowers-specs/2026-08-24-carton-opt-3d-hierarchy-design.md`).*

### 4. Projekty A/B (redesign)

- Formalny projekt zmiany opakowania (`PackagingRedesign`): **wersja obecna (A)
  vs docelowa (B)** side-by-side — dwie pełne paletyzacje 3D liczone silnikiem
  (`_engine_pallet`) + spec inżynierski (`_pallet_spec.html`): kartony/warstwę ×
  warstwy, wysokość vs max, waga, pokrycie podłogi, nawis, **środek ciężkości
  i ocena stabilności** (stabilny/uwaga/ryzyko).
- **Live-edit B**: zmiana wymiarów przelicza paletę 3D i spec na żywo
  (endpoint `carton_opt_redesign_metrics`).
- **Orientation explorer**: „który bok kartonu do góry" — warianty orientacji
  pionowej z best-fill, „Ustaw jako B" (`_orientation_options`).
- Wejścia: z auto-sugestii („Załóż Projekt A/B") albo ręcznie.

## Silnik (wspólny — zero duplikacji geometrii)

Wszystko liczy jeden framework-free silnik `palletizer`
(`PalletCalculator` + MaxRects/heurystyki):

| Helper | Rola |
|---|---|
| `_variant_fill(l,w,h,instr)` | fill + kartony/paletę dla wymiarów kartonu |
| `_engine_pallet(base,l,w,h)` | pełna paletyzacja (three_data + spec) bez zapisu |
| `pack_into(container,unit)` | bezwymiarowy packer poziomu hierarchii |
| `_hierarchy_levels(...)` | 4 poziomy z kaskadą i nadpisywaniem wymiarów |
| `_suggest_dims(...)` | top-K lepszych wymiarów w paśmie objętości |

Gotcha: `unit_weight_kg=max(0.001, …)` — walidacja silnika odrzuca wagę 0,
więc materiały bez wagi straciłyby fill/3D bez tego zabezpieczenia.

## Konfiguracja i role

- `OptimizationConfig` (singleton `load()`): `min_fill_pct` + pasmo objętości
  sugestii (`suggest_vol_down_pct`/`suggest_vol_up_pct`) — edycja tylko admin,
  inline na dashboardzie Pilności.
- Odczyt ekranów: rola modułu (`module_required("carton_opt")`); akcje
  (warianty, promocja, statusy zgłoszeń) — `@_optimizer`.

## Czego moduł świadomie NIE robi

- **Nie pisze do SAP** (zasada repo: SAP read-only) ani do master-danych opakowań
  poza nową wersją instrukcji (rozszerzenie promocji = Etap 2).
- **Mieszane orientacje w jednej palecie** (np. 4 płasko + 2 pionowo) — odłożony
  epik R&D; silnik warstwowy (identyczne warstwy ×N, Placement2D bez osi Z)
  tego nie umie.
- **Otwarte kartony 3D** — zbudowane i wycofane (#308→#309); renderer pokazuje
  zamknięte bryły z grafiką. Nie wracamy do tego.
