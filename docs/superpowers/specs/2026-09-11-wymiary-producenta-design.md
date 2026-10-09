# Moduł „Wymiary producenta" — porównanie wymiarów kartonu producent vs master data

**Data:** 2026-09-11
**Autor:** Tomek + Claude
**Status:** zaakceptowany (spec)
**Obszar:** `web/ui` (app core), ekran w Data Center, rola Master Data

## Cel

Wgrać deklarowane przez producentów wymiary kartonu (58 plików „packaging size",
jeden = jeden dostawca), porównać je z **naszą** master data per REF, oznaczyć
rozjazdy i automatycznie założyć zdedublowane Taski dla realnych rozbieżności.

To odpowiednik istniejącego mechanizmu **packspec** (declared-vs-master + raport z
odznakami + zgłoszenie), ale dla źródła „wymiary kartonu od producenta".

## Zakres źródeł danych

- **Strona producenta (import):** 58 plików `.xlsx`, arkusz `dane opakowań`, wspólny
  szablon 14-kolumnowy (55/58 identyczny nagłówek, 3 kosmetyczne odchyłki). Klucz = **REF**.
  Wymiary kartonu = 3 osobne kolumny numeryczne (lenght / width / height, cm),
  ~98% czysto numeryczne. 1569 wierszy łącznie. Kolumny box/pouch to stringi (mm/cm,
  różne separatory) — **nie** są porównywane, przechowywane tylko do podglądu.
- **Strona „nasza" (autorytatywna):** master data już w bazie —
  `PalletizationInstruction.carton_l/w/h` (IntegerField cm) z fallbackiem na
  `MaterialReference.width_cm/height_cm/length_cm` (mirror SAP MARM). **Nie** importujemy
  pliku NAKE_KAR — to i tak eksport SAP, który wpada do bazy tą samą drogą.

## Architektura

### 1. Model danych — `web/ui/models/producer_dims.py`

- **`ProducerCartonBatch`** — jedna partia = jeden plik dostawcy.
  Pola: `supplier` (etykieta z nazwy pliku), `source_filename`, `uploaded_at`,
  `uploaded_by` (FK user), `is_active` (bool), `row_count` (int).
  Reguła: ponowne wgranie pliku danego dostawcy → poprzednia aktywna partia tego
  dostawcy dostaje `is_active=False` (wzorzec packspec/ImportRun).
- **`ProducerCartonDim`** — pojedynczy wiersz.
  Pola: FK `batch`, `supplier` (denorm dla filtra/dedup), `ref_code` (surowy string),
  FK `product` (nullable — z `resolve_product_code`), `carton_l` / `carton_w` /
  `carton_h` (FloatField cm, null gdy brak/tekst), `qty_in_carton` (int null),
  `gross_kg` / `net_kg` (Float null), `box_size_raw` / `pouch_size_raw` (CharField,
  podgląd), `description` (CharField opcjonalnie).

Migracja w `web/ui/migrations/` (numer na koniec łańcucha; guard migracji przy merge).

### 2. Rdzeń porównania — `web/ui/producer_dims.py` (czysta funkcja, testowana)

```
compare_dims(producer_lwh, ours_lwh, *, min_cm=1.0, pct=0.05)
    -> (verdict, max_delta_cm)
    verdict ∈ {"ok", "mismatch", "no_data"}
```

- Sortuje oba tryplety malejąco, porównuje **pozycyjnie** (zbiór, nie oś-w-oś) —
  karton 53.5×41×37 == 41×37×53.5. Rozjazd = realnie inny karton, nie inna etykieta osi.
- Tolerancja per oś: `max(min_cm, pct * wymiar_naszy)`. Przekroczenie na którejkolwiek
  osi → `mismatch`.
- Brak którejkolwiek strony (None/tekst `-`/`/`/puste, brak `Product`, brak dims w
  master data) → `no_data` (nie liczy się jako rozjazd, nie tworzy Taska).
- Próg konfigurowalny (`settings` / `OptimizationConfig`), domyślnie 1 cm / 5%.

To jest **seam** modułu: mały interfejs, cała logika porównania za nim, testowana
bezpośrednio przez ten interfejs (bez HTTP, bez bazy).

### 3. Import — `web/ui/views/producer_dims.py`

Widok `producer_dims_upload`, `@_master_data`, `@require_POST`.

- **Multi-file upload** (`request.FILES.getlist(...)`). Per plik:
  - `openpyxl.load_workbook(f, read_only=True, data_only=True)`, arkusz `dane opakowań`
    (fallback: pierwszy arkusz), `iter_rows(values_only=True)`.
  - Dane od wiersza 5 (indeks 4) — 3 wiersze nagłówka pomijane.
  - **Odczyt pozycyjny**: REF = kol B (idx 1), carton L/W/H = kol I/J/K (idx 8/9/10),
    qty_in_carton = L (11), gross = M (12), net = N (13), box_raw = G (6),
    pouch_raw = D (3), description = C (2).
  - **Sanity-check nagłówka**: w wierszu 3 (idx 2) kol B znormalizowana == `"ref"`;
    inaczej plik odrzucony z `messages.error` (ochrona przed złym plikiem/mapowaniem).
  - Wymiary nienumeryczne (`-`, `/`, tekst) → `None`.
  - Dostawca = z nazwy pliku (bez `packaging size`, rozszerzenia, myślników).
  - `resolve_product_code(ref)` → `product` (lepsze niż surowy `code__iexact`;
    zdejmuje prefiksy NIEAKT/ACME, aliasy).
- `transaction.atomic`: dezaktywuj poprzednią aktywną partię dostawcy → `bulk_create`
  wierszy → `ImportRun.record(kind="producer_dims", rows=, label=supplier, user=)`.
- **Generowanie Tasków (na import, dedup)**: po zapisie policz werdykty; dla każdego
  `mismatch` utwórz Task wzorcem `notifications._raise_task`:
  `category="carton_dim_mismatch"`, `dedup_key=f"cartondim:{supplier}:{ref_code}"`,
  `priority="high"`, odbiorcy = `owner_users()` (Master Data + Admini).
  Dedup: jeśli otwarty Task z tym `dedup_key` istnieje — pomiń. `ok`/`no_data` nie
  tworzą nic. Liczba Tasków = liczba realnych rozjazdów, nie 1569.

### 4. Raport — `web/ui/views/producer_dims.py`

Widok `producer_dims_list`, `@_master_data`, szablon
`web/ui/templates/ui/producer_dims/list.html` (Polish-first, branding GROOVE, tokeny).

Tabela: REF · dostawca · wymiary producenta (L×W×H) · nasze wymiary (L×W×H, źródło:
instrukcja / MARM) · delta (max cm) · odznaka (OK / rozjazd / brak danych).
Filtry: dostawca (dropdown), status (ok/mismatch/no_data). Podsumowanie liczników.
Paginacja (≥1569 wierszy). Werdykt liczony na render (join aktywnych
`ProducerCartonDim` → `product.latest_instruction()`), z guardem N+1
(`select_related`/prefetch instrukcji).

Wejście: kafel/link z ekranu Data Center (obszar Master Data). URL-e w namespace
`ui:` (`ui:producer_dims`, `ui:producer_dims_upload`).

### 5. Testy — `web/ui/tests/test_producer_dims.py`

- `compare_dims`: równe po transpozycji = `ok`; poza tolerancją = `mismatch`; małe
  pudełko (8 cm, +0.6 cm) chronione stałą 1 cm = `ok`; brak strony = `no_data`;
  duży karton (60 cm, +2 cm < 5%) = `ok`.
- Import: parsowanie 3-wierszowego nagłówka i odczyt pozycyjny; REF→Product przez
  `resolve_product_code`; dezaktywacja starej partii dostawcy przy reimporcie;
  odrzucenie pliku bez nagłówka `ref`.
- Task: `mismatch` tworzy jeden Task; reimport tego samego dostawcy nie duplikuje
  (dedup po REF+dostawca); `ok`/`no_data` = zero Tasków.

## Reuse (istniejące wzorce)

- `ui/product_codes.resolve_product_code` — join REF→Product.
- `openpyxl` (hard dep) + wzorzec importu z `views/packspec.py` / `core/xlsx.py`.
- `ImportRun.record` — audyt partii (packspec.py).
- `ui/notifications.py` `_raise_task` / `owner_users` / dedup po `dedup_key`
  (silnik jak `stock_discrepancy`).
- Wzorzec ekranu raportu z odznakami diff — `views/packspec.py` + `packspec/list.html`.

## Świadome pominięcia (YAGNI)

- Brak importu pliku NAKE_KAR — „nasze" wymiary to master data w bazie.
- Box/pouch nieporównywane (tylko podgląd); porównujemy wyłącznie karton L/W/H.
- Brak eksportu XLSX rozjazdów i brak write-backu do master data (master data nie
  jest nadpisywana automatycznie — spójne z carton_opt/packspec).
- Brak dwuetapowego preview-before-commit (import commituje od razu, jak packspec).

## Definition of Done

Gałąź `claude/producer-carton-dims` → migracja (guard) → `manage.py check` →
`ui.tests.test_producer_dims` + pełne `ui.tests` (env CI) → PR na `main` → auto-merge
→ deploy. UI: Polish-first, tokeny GROOVE, świadomy design (nie domyślny szablon).
