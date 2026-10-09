# Testy GROOVE — jak uruchamiać i na czym budować

System testów regresji: plan i decyzje w [`PLAN-ETAP-0.md`](PLAN-ETAP-0.md), macierze uprawnień w
[`permissions.yaml`](permissions.yaml) / [`ui-permissions.yaml`](ui-permissions.yaml), znalezione
błędy aplikacji w [`../BUGS-FOUND.md`](../BUGS-FOUND.md). Ten plik opisuje **infrastrukturę (etap 1)**.

## Polecenia

| Cel | Linux / CI / Git Bash | Windows cmd |
|---|---|---|
| szybkie (palletizer + check + Django, `--failfast`) | `make test` | `web\scripts\test.bat fast` |
| wszystko (+ limit 500 linii, `makemigrations --check`, pokrycie, strażnik liczby testów) | `make test-full` | `web\scripts\test.bat full` |
| jedna persona (testy z tagiem `persona`) | `make test-role ROLE=transport` | `web\scripts\test.bat role transport` |
| PostgreSQL 17 jak produkcja | `make test-pg` | `docker compose -f docker-compose.test.yml up -d --wait db` + `set DATABASE_URL=postgresql://palviz:palviz@127.0.0.1:55433/palviz_test` + `test.bat full` |
| wybrane moduły | `sh web/scripts/test.sh ui.tests.test_x` | `web\scripts\test.bat ui.tests.test_x` |
| pytest (alternatywnie) | `pytest web/ui/tests/test_testkit_infra.py` | to samo |
| wizualne (Playwright `toHaveScreenshot`, 11 widoków × desktop/ciemny/telefon) | `make test-visual` | `cd tests\e2e && npm ci && npx playwright test --grep-invert @flow` |
| nowe wzorce po świadomej zmianie wyglądu | `make test-visual-update` | `npx playwright test --grep-invert @flow --update-snapshots` |
| przepływy E2E (`@flow`, DOM/tekst, bez zrzutów) | `make test-flow` | `cd tests\e2e && npm ci && npx playwright test --grep @flow` |

Persony (`ROLE=`): `anon nieaktywny bez_roli superuser admin master_data transport kontrola_hu lider
podglad obsluga_klienta magazyn optymalizacja zablokowany nadpisanie_modulu` — albo pełna nazwa
grupy („Kontrola HU”). Raport pokrycia HTML: `htmlcov/index.html` po `test-full`.

### Testy wizualne — `tests/e2e/`

`prepare.py` tworzy świeżą bazę SQLite (`migrate` → `seed_testdata`), przestawia daty `auto_now*` na
stały dzień i zapisuje sesje person; potem Playwright startuje `runserver` (:8899) i porównuje zrzuty
z `tests/e2e/__screenshots__/<projekt>/`. Maskowane są treści zależne od czasu i sieci (kurs NBP, czasy
SLA). Wzorce są robione **na Windows** (renderowanie fontów zależy od systemu), więc suita jest lokalna
— nie w CI (runner bez przeglądarek, decyzja P-6). Po celowej zmianie UI: `make test-visual-update`,
obejrzyj różnice w `test-results/` i zacommituj nowe wzorce razem ze zmianą.

### Przepływy E2E — `tests/e2e/flow.spec.ts` (tag `@flow`, TEST-005)

Te same `prepare.py` + `runserver`, ale asercje na DOM/tekście — **bez zrzutów**, więc działają na
każdym OS. Scenariusze: logowanie formularzem (Transport → kafle huba swojej roli; kontroler HU →
prosto na skaner), kontrola HU na skanerze (skan → start → liczenie → zaksięgowanie → podgląd „HU
zgodny”), kalkulator paletyzacji (zmiana parametru → wynik htmx), rola Podgląd → 403 na ekranach
planera. Własny projekt `flow` w `playwright.config.ts` (projekty wizualne mają `grepInvert: /@flow/`).
Testy zmieniają bazę (księgują HU), więc każdy przebieg startuje na świeżej bazie. Wymaga
vendorowanych bibliotek (`sh web/scripts/fetch_vendor.sh` — bez htmx kalkulator nie liczy).
**CI:** `.github/workflows/e2e-nightly.yml` — co noc (02:30 UTC) + ręcznie (`gh workflow run
e2e-nightly.yml`), `ubuntu-latest` (runner self-hosted nie ma przeglądarek), SQLite; raport HTML +
trace jako artefakt przy porażce. Nie jest bramką PR-ów.

## Infrastruktura — `web/testkit/`

| Moduł | Co daje |
|---|---|
| `runner.py` | `TEST_RUNNER` (`settings.py`): blokada sieci także w procesach `--parallel`, szybki hasher haseł (MD5 tylko w testach), wypisuje wersję bazy (`[testkit] baza testowa: …`) |
| `net.py` | każde połączenie poza loopback → `NetworkBlocked` (+ lista prób `net.blocked`) |
| `fake_http.py` | `FakeHTTP` — jedna tabela tras dla `requests`, `urllib.request.urlopen`, `httpx` |
| `integrations.py` | nagrane odpowiedzi: NBP, Google Maps, Twilio, Teams, n8n, Master Data API, Power BI (DAX), OIDC, Anthropic, OpenAI, Azure OpenAI, Ollama × `ok / http_4xx / http_5xx / timeout / empty / bad_format`; `fake_msal`, `capture_sentry`, `FailingEmailBackend` (SMTP); `INTEGRATION_SETTINGS` |
| `clock.py` | `frozen("dst_jesien")` (time-machine), `KEY_DATES` (przeszłość, dziś, przyszłość, przełom roku, obie zmiany czasu), `warsaw(...)` |
| `factories.py` | factory_boy dla modeli domenowych; polskie znaki domyślnie, `long_text(n)` na limit kolumny |
| `personas.py` | 15 person: 13 z `permissions.yaml` + `zablokowany` (axes) + `nadpisanie_modulu` (`UserModuleAccess`); `client_for()`, `login_form()` |
| `seed.py` | `seed_all()` / `SeedDataMixin` / `manage.py seed_testdata` (tylko DEBUG) — pełny zestaw danych |
| `queries.py` | `QueryScalingMixin.assertQueriesFlat(url, grow)` — strażnik N+1: ekran listy robi tyle samo zapytań przy 1 i przy N wierszach (porównanie względne, niezależne od bazy; audyt PERF-006) |

Przykład:

```python
from django.test import TestCase
from testkit.seed import SeedDataMixin
from testkit.personas import client_for, selected
from testkit import integrations as it

class MojeTesty(SeedDataMixin, TestCase):          # self.data: users, catalog, hu, shipments, …
    def test_hu_lidera(self):
        r = client_for("lider").get("/control/")
        ...

    def test_nbp_pada(self):
        with self.settings(**it.INTEGRATION_SETTINGS), it.integration("nbp", "timeout"):
            ...
```

Zasady: **żadnych prawdziwych wywołań sieciowych** (blokada to wymusza); znany błąd aplikacji →
test z `@unittest.expectedFailure  # B-xxx` + wpis w `BUGS-FOUND.md`, bez naprawy bez zgody;
nowe pliki `.py` ≤ 500 linii; testy backendu w `web/<app>/tests`.

„Dziś” w testach to dzień lokalny (Europe/Warsaw): `timezone.localdate()` albo zamrożony zegar
`testkit.clock.frozen()` — nigdy `date.today()`, `datetime.now()` ani `timezone.now().date()`
(ta ostatnia to dzień UTC, inny niż lokalny między 22:00/23:00 a północą UTC). Pilnuje tego
`ui.tests.test_date_boundaries` (TEST-008).

## Bazy danych

- Domyślnie **SQLite w pamięci** (szybko, bez zależności).
- **PostgreSQL 17** (wersja produkcji): `docker-compose.test.yml` — `127.0.0.1:55433`, dane w tmpfs,
  usługa `migrate` robi migracje + `seed_testdata` na bazie `palviz_test` (do ręcznego klikania /
  E2E); `manage.py test` tworzy obok własną `test_palviz_test`.
- **CI** (self-hosted runner, bez Dockera dla runnera): stały kontener `palviz-ci-postgres`
  **postgres:17-alpine** na `127.0.0.1:55432` — ta sama wersja co produkcja (od 2026-09-28,
  TEST-007).

## Punkt odniesienia (etap 1)

Pomiar lokalny 2026-09-26: Windows 11, Python 3.13, 12 wątków, SQLite w pamięci, `--parallel auto`,
z pokryciem (`coverage`, konfiguracja z `pyproject.toml`). „Przed” = `main` a0eb841, to samo polecenie.

| | przed etapem 1 | po etapie 1 (`make test-full`) |
|---|---|---|
| testy Django | 1 782 OK (2 pominięte) | 1 834 OK (2 pominięte, 3 `expectedFailure` = B-009…B-011) |
| testy palletizer | 32 (10 s) | 32 (7 s) |
| zestaw Django — sam przebieg | 194,6 s | **104,3 s** (−46 %: szybki hasher haseł w testach) |
| zestaw Django — z tworzeniem baz | 243 s | 147 s |
| pokrycie łącznie | 82,9 % (21 531 instr.) | 83,3 % (21 549 instr.) |
| — `ui` / `huctl` / `transport` / `wh3d` / `palletizer` | 78,8 / 91,1 / 86,4 / 86,4 / 82,9 % | 79,4 / 91,3 / 87,7 / 86,4 / 82,9 % |

PostgreSQL 17.11 (`docker-compose.test.yml`, `--parallel 4`): 1 834 OK w 159,5 s; migracje od zera +
`seed_testdata` na czystej bazie — OK.

Blokada sieci ujawniła, że testy ekranów shipmentów (`transport.tests.test_shipment_planner_ui`,
`test_shipment_calc_more`, `test_import_status`) **wołały prawdziwe API NBP** (w CI szły do internetu),
a `ui.tests.test_powerbi_import_cache` — prawdziwe `login.microsoftonline.com`. Pierwsze działają teraz
deterministycznie w trybie „NBP offline” (kod ma fallback, patrz B-011), drugie dostały nagraną
odpowiedź discovery Entra.
