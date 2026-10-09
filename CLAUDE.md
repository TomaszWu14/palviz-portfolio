# CLAUDE.md

Guidance for AI assistants (and humans) working in this repository.

## What PalViz is

PalViz is a warehouse / palletization planning application for **ACME
Group**. It started as a CLI palletization calculator (`main.py` + the `palletizer`
package) and grew into a full Django web app (`web/`) covering product master data,
carton/pallet optimization, warehouse 3D maps, shipments & freight quoting, driver
dispatch, slotting/picking analytics, and a barcode-scanner Handling-Unit (HU) control
module.

The UI language is **Polish** — labels, messages, model `verbose_name`s, and most
code comments are in Polish. Keep new user-facing strings in Polish to match.

The product is branded **GROOVE** (the umbrella platform). Branding is env-driven: the
`APP_NAME` setting (default `GROOVE`) is injected into every template as `{{ app_name }}`
via `core.context_processors.branding` — use it for new user-facing brand text rather than
hardcoding a name. GROOVE is a **single-repo modular monolith** (see `ARCHITECTURE.md`):
after login the hub (`module_home` → `ui/home.html`) shows the platform modules ("tabs")
the user may open — role-gated tiles defined in `web/ui/platform_modules.py`
(`MODULES` / `modules_for(user)`) and documented in `ARCHITECTURE.md` §Hub i moduły
(the authoritative, always-current module list and role table — don't hardcode a
module count or list here, it drifts). To add/rename a module, edit `MODULES` (key,
name, entry `url_name`, icon, colour, roles). One dedup quirk worth knowing:
**Baza klientów** (`klienci`) is a Data Center sub-screen, so `modules_for` only
surfaces it as a standalone hub tile for roles that lack Data Center (Transport and
the narrow "Obsługa klienta" role). Internal identifiers (the `palletweb` project, the
`ui` app label, the `palletizer` package, `palviz-three.js`) keep their names — they're
not the brand.

There are two largely independent code surfaces that share the same packing core:

1. **`palletizer/`** — a standalone, framework-free Python package: pure packing
   geometry, domain dataclasses, CSV/CLI IO, and matplotlib/plotly visualization.
   Driven by `main.py`. Has no Django dependency.
2. **`web/`** — the Django project (`palletweb`) with four domain apps plus a shared kernel: **`ui`** (the core —
   master data, palletization, tasks, ZARIA, PWA, plus shared infra: roles, api,
   middleware, context processors), **`wh3d`** (warehouse 3D map/editor/heatmap),
   **`huctl`** (Kontrola HU scanner module), and **`transport`** (shipments, quoting,
   driver dispatch). It imports `palletizer` as a local dependency for layer-packing
   math. Shared kernel **`core`** (roles, middleware, context processors, hub module
   registry, health) has no models. Tests live per app: `ui/tests`, `wh3d/tests`,
   `huctl/tests`, `transport/tests` (~295 modules).

## Layout

Szukasz, gdzie napisać/znaleźć dokument (ADR, spec, mapa procesu, archiwum, …)? Patrz
[`docs/README.md`](docs/README.md) — mapa typ dokumentu → katalog.

```
main.py                     CLI entrypoint for the standalone palletizer
ARCHITECTURE.md             Platform architecture notes (modular monolith, hub pattern)
pyproject.toml              Dev-tool config (ruff, mypy, pytest, coverage, bandit)
requirements.txt            Runtime deps; requirements-dev.txt = dev/CI aids
palletizer/                 Framework-free packing library
  domain/                   Dataclasses: CartonVariant, PalletType, Dimensions
  config.py                 Pallet presets (EU 120×80, Z129 legacy alias)
  services/
    pallet_calculator.py    The packing engine (facade; orchestrates the modules below)
    maxrects.py             MaxRects rectangle packing
    layer_geometry.py       Layer geometry primitives
    layer_patterns.py       Layer pattern generators (grid, brick, pinwheel, …)
    ortools_layer.py        Optional OR-Tools CP-SAT solver for an optimal single layer
    vehicle_load.py         Vehicle/shipment consolidation
  io/                       csv_loader, cli_input, parsing (European-comma tolerant)
  visualization/            plot_2d (PNG), plot_3d (HTML)
  tests/                    unittest tests for the packing logic (incl. property-based
                            test_maxrects_property.py via hypothesis)
web/
  manage.py
  palletweb/                Django project: settings, urls, wsgi, celery
    config.py               Typed/validated env config (pydantic-settings, fail-fast)
  ui/                       Core Django app (master data, palletization, tasks, ZARIA)
    models/                 Domain-split package: catalog, packaging, packspec,
                            customers, warehouse, carton_opt, comms, tasks_users,
                            integrations, zaria (re-exported via __init__.py)
    views/                  Feature-split view package (see below)
    forms.py, forms_calc.py, forms_master.py, forms_transport.py
    admin.py, roles.py, oidc.py, tasks.py, powerbi.py
    apps.py, signals.py     AppConfig.ready() wires signals (auto-create UserProfile)
    middleware.py           Request-ID (correlation) + security-headers (CSP report-only)
    platform_modules.py     GROOVE hub module registry (MODULES / modules_for)
    notifications.py        Notifications + stock-discrepancy → task engine
    context_processors.py   Injects role flags + branding into every template
    api.py, nbp.py, slotting.py, labels.py,
    tables.py, filters.py, health_urls.py
    templatetags/           palviz_extras
    management/commands/    create_roles, import_locations, powerbi_connect
    migrations/             ~190 migrations
    templates/ui/           Templates grouped by feature
    static/ui/              JS (vendored three.js/echarts/plotly), icons, css
    tests/                  Django tests for ui (other apps have their own tests/)
  wh3d/                     App: warehouse 3D map/editor/heatmap/rack types
    models.py, views/       warehouse_map*, warehouse_editor, warehouse_heatmap,
                            warehouse_model, warehouse_master, warehouse_racktype,
                            locations_master
  huctl/                    App: Kontrola HU (scanner)
    models*.py, views/      hu, hu_control, hu_count, hu_queue, hu_leader,
                            hu_transaction*, hu_print, hu_reports, hu_stock, …
  transport/                App: shipments, quoting, drivers
    models*.py, views/      shipments*, quotes, driver, carriers, readiness,
                            mailing, kpi, docs, customers, imports_excel
  scripts/fetch_vendor.sh   Vendors front-end JS libs locally (no runtime CDN)
                            (path: web/scripts/fetch_vendor.sh)
android/                    TWA (Trusted Web Activity) APK wrapper docs/manifest
tools/sap_marm_to_palviz.py SAP MARM → PalViz data conversion helper
scripts/backup.sh           DB + media backup (Postgres pg_dump / SQLite .backup)
excel_templates/, data/     Sample/template spreadsheets and CSV
.env.example, .pre-commit-config.yaml
Dockerfile, docker-compose.yml, docker-entrypoint.sh
run.bat, setup.bat          Windows local-dev convenience scripts
```

### The `views/` packages

Views were split from a former monolithic `views.py`. Each app (`ui`, `wh3d`, `huctl`,
`transport`) has a `views/` package; each feature lives in its own module (<500 lines)
and `views/__init__.py` re-exports everything with `from .x import *`. Shared imports,
constants, and helpers live under `ui/views/core/`:

- `core/base.py` — shared imports/constants/helpers + role decorators (re-imported from
  `roles.py`) used across all apps' views; constant tables in `core/base_tables.py`
- `core/helpers*.py` — split helpers, e.g. the shipment 3D packers in
  `core/helpers_shipment_three.py` (`_build_shipment_three_data`, `…_py3dbp`, `…_ffd`)
- `core/packing*.py`, `core/figures*.py` (plotly/matplotlib figures), `core/xlsx.py`
  (Excel export), `core/images.py`

`ui/views/` feature modules include: `data_center`, `products*`, `cartons`,
`inner_packs`, `categories`, `instructions`, `calc`, `carton_opt*`, `packspec`, `phv*`,
`locations`, `warehouse_search`, `slotting`, `reports`, `tasks`, `zaria*`, `admin*`,
`pwa`, `imports_*`, `artwork`, `misc`. Warehouse-3D views are in `wh3d/views/`, HU
views in `huctl/views/`, shipment/quote/driver views in `transport/views/`.

When adding a view: put it in the right app's feature module, register the URL in
`web/ui/urls.py` (all URLs stay in the `ui` namespace, so reverse as `ui:<name>`), and
guard it with the appropriate role decorator from `roles.py`.

## Domain & packing engine

- `palletizer/domain/` defines `CartonVariant` (SKU + dims + weights + demand),
  `PalletType`, and `Dimensions` as frozen dataclasses with `validate()` methods.
  Units: dimensions in **cm**, weights in **kg**, quantities in **pieces**.
- `palletizer/config.py` holds pallet presets. The real pallet is **EU 120×80**;
  `Z129` is a legacy alias (resolves to the same EU geometry) kept so old DB rows still
  resolve — don't remove it.
- `services/pallet_calculator.py` is the heart of the app. `PalletCalculator.calculate`
  validates, asserts the carton fits, then `generate_layout_options` produces up to 15
  visually **distinct** layer arrangements (uniform grid, rotated, mixed stripes,
  MaxRects with several heuristics, brick/interlock, block splits, pinwheel, column
  packing) and ranks them by cartons-per-layer then area utilization.
- `services/ortools_layer.py` is an **optional** CP-SAT path (OR-Tools) for a provably
  optimal single layer; it is lazy-imported so the engine still runs when OR-Tools is
  absent.
- The web app reuses this engine; the 3D shipment packer (`_build_shipment_three_data_py3dbp`)
  builds mono-SKU pallets first and only mixes leftovers.

When changing packing logic, run `palletizer/tests` AND `web/ui/tests` (the Django
side encodes warehouse rules like "a dominant SKU is never scattered across mixed
pallets").

## Roles & access control

Authorization is group-based — see `web/ui/roles.py`. **Nine** Polish-named groups:
`Administratorzy`, `Master Data`, `Transport`, `Kontrola HU`, `Lider kontroli`,
`Podgląd`, `Obsługa klienta` (a narrow role limited to the customer database),
`Magazyn` (warehouse operator: HU label printing), and `Optymalizacja kartonów`
(carton-fill optimization operator).
Superusers always pass. Guard views with `@role_required(...)` (or the convenience
decorators defined at the bottom of `roles.py`: `_admin_only`, `_master_data`,
`_transport_mgr`, `_controller`, `_leader`, `_md_or_tr`, `_md_or_control`,
`_customer_mgr`, `_any_role`). `MODULE_ROLES` maps fine-grained write capabilities to
groups. Templates get boolean role flags via the `core.context_processors.user_roles`
context processor (computed in a single query — prefer those flags over calling
`has_role` repeatedly).

Seed the groups with `python manage.py create_roles`. Control-only operators
(`is_control_only` — a controller/leader with no planner access) are routed straight to
the HU scanner on login.

**The group-name strings in `roles.py` are a frozen contract.** The literal values
(`"Administratorzy"`, `"Master Data"`, …) are `auth_group` DB rows **and** the vocabulary
the SSO backend (`oidc.py`) maps IdP claims onto, imported by 9+ modules. Renaming a value
silently strips access and breaks SSO sync — refactor the Python identifier freely, never
the string. To retire a group, migrate `auth_group` rows + the SSO claim mapping first.
`web/ui/tests/test_group_contract.py` pins the exact values and fails CI on any change.

## Graph structure & module seams

Findings from the graphify dependency-graph analysis (verified 2026-07-24), useful before
any "harden the architecture" refactor. Graf w `graphify-out/` jest generowany **lokalnie**
(`graphify update .`, narzędzie niedostępne w CI) i commitowany; workflow
`graph-freshness.yml` otwiera issue `graf-wiedzy`, gdy graf odstaje od `main` — zanim
zaufasz odpowiedzi grafu, porównaj „Built from commit" w `graphify-out/GRAPH_REPORT.md`
z `git rev-parse HEAD`:

- **The high-traffic god nodes are wide hubs, not structural bridges.** `Product`,
  `PalletizationInstruction`, and `Shipment` rank high on betweenness (lots of paths run
  through them) but removing any one disconnects nothing — the graph routes around them.
  High betweenness = traffic, not a single point of failure.
- **Only two genuine cross-community cut-vertices exist** (out of 678 articulation points):
  `palletizer/services/vehicle_load.py` — the sole tie-in for the Vehicle Load Planning
  subsystem — and `web/ui/roles.py` — the sole tie-in for the SSO cluster (`oidc.py`
  imports the group vocabulary from it).
- **These are clean seams, not fragility — do not add edges to "harden" them.**
  `vehicle_load.py` is a pure leaf facade (only `math`/`typing`, one consumer); `roles.py`
  is a shared kernel of constants. The single seam is the correct, minimal dependency in
  both cases. Adding a second edge just to lower an articulation-point count trades real
  coupling for a metric that doesn't indicate a real problem.

## Running & developing

### Standalone palletizer (CLI)
```bash
python -m venv .venv && source .venv/bin/activate    # Windows: setup.bat
pip install -r requirements.txt
python main.py --mode csv --input data/produkty.csv --pallet EU --max-height 180 --export-viz 1
```
Outputs `output/wynik.csv`, `output/warianty.csv`, and per-carton PNG/HTML viz.
`main.py` accepts `--mode {csv|cli}`, `--input`, `--pallet {EU|Z129}`, `--max-height`,
`--length`, `--width`, `--export-viz {0|1}`, `--render-layers N`.

### Django web app (local)
```bash
cd web
python manage.py migrate
python manage.py createsuperuser   # or: setup.bat on Windows
python manage.py create_roles      # seed the nine role groups
python manage.py runserver 8080    # or: run.bat (port 8080)
```
App at `http://localhost:8080`, admin at `/admin`, branded login at `/login/`.

In local dev set `DJANGO_DEBUG=true`. With DEBUG on, Celery runs **eagerly**
(synchronously) — no worker/Redis needed. The front-end 3D libraries are vendored;
run `sh web/scripts/fetch_vendor.sh` once if `static/ui/vendor/` is empty.

### Docker (production-like)
```bash
docker compose up --build
```
Brings up gunicorn (port 8000) + Redis. `docker-entrypoint.sh` runs migrations then
gunicorn. Static is collected at build time; 3D libs are vendored in the image.

### Dev tooling (optional)
`requirements-dev.txt` adds developer aids configured (leniently) in `pyproject.toml`:
**ruff** (lint/format, selects pyflakes `F` + bugbears `B`), **mypy** (lenient),
**pytest/pytest-django** (a convenience runner; CI still uses `manage.py test`),
**bandit** + **pip-audit** (security), `factory_boy`/`hypothesis`. These are intentionally
non-blocking so they never gate the auto-merge pipeline. A `.pre-commit-config.yaml`
wires the same linters as git hooks (`pip install pre-commit && pre-commit install`).
Uwaga: `pre-commit install` wyklucza się z `git config core.hooksPath .githooks`
(ścieżka aktywująca opcjonalny sync grafu do Obsidiana) — wybór i zalecenia opisuje
README, sekcja „Git hooks i graf wiedzy (Obsidian)".

## Tests

CI (`.github/workflows/ci.yml`) gates the merge on these — run them
before pushing:
```bash
# Packing library (framework-free)
python -m unittest discover -s palletizer/tests -p "test_*.py"

# Django side (run from web/, needs env vars)
cd web
DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' \
  PYTHONPATH=.. python manage.py check
DJANGO_SECRET_KEY=ci-test-secret DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' \
  PYTHONPATH=.. python manage.py test ui.tests wh3d.tests huctl.tests transport.tests -v 1
```
The Django app must be able to import `palletizer`, hence `PYTHONPATH` points at the
repo root. Tests use Django's `unittest`-style runner (no pytest config is required in
CI). The ~295 test modules (split per app: ui / wh3d / huctl / transport) are mostly
`SimpleTestCase` smoke/render tests plus targeted logic tests (packing, roles,
shipments, HU control, warehouse, imports, Power BI, tasks, ZARIA).

**Shortcut:** `sh web/scripts/test.sh [labels…]` (Windows cmd: `web\scripts\test.bat`)
sets the env, runs `manage.py check`, then the given test labels (no labels = full
`ui.tests` suite with `--parallel auto`). Modes: `make test` / `test.bat fast` (quick),
`make test-full` / `test.bat full` (+ coverage, 500-line gate, migrations check, test-count
guard), `make test-role ROLE=transport` / `test.bat role transport`, `make test-pg`
(PostgreSQL 17 from `docker-compose.test.yml`).

**Test infrastructure (`web/testkit/`, see `tests/README-TESTS.md`):** the `TEST_RUNNER`
blocks every non-loopback network connection and uses a fast password hasher; use
`testkit.factories` / `testkit.personas` / `testkit.seed.SeedDataMixin` for data,
`testkit.integrations` for recorded external responses (never real calls), and
`testkit.clock.frozen()` for time (Europe/Warsaw, DST/year-end dates). Test deps live in
`requirements-test.txt` (CI installs it; prod image does not).

**Test router — in the dev loop run only the tests for the area you touched; the full
suite runs before the PR (and in CI):**

| Changed area | Run first (`test.sh` labels, fully-qualified `<app>.tests.<module>`) |
|---|---|
| `palletizer/` | `python -m unittest discover -s palletizer/tests` + `test_packing test_calc_optimal_layer` |
| `huctl/` (Kontrola HU) | `huctl.tests.test_hu_control huctl.tests.test_hu_count_gates huctl.tests.test_hu_queue_order huctl.tests.test_hu_leader_panel huctl.tests.test_kpi_by_zone huctl.tests.test_scan_enforce` |
| `transport/` | `transport.tests.test_shipment_calc ui.tests.test_shipment_ffd transport.tests.test_quote transport.tests.test_quote_calc transport.tests.test_driver huctl.tests.test_incomplete_shipment` |
| `wh3d/` | `wh3d.tests.test_warehouse_layout wh3d.tests.test_warehouse_model_view wh3d.tests.test_warehouse_builder wh3d.tests.test_map_locations_json ui.tests.test_wh3d_boundary` |
| `ui/roles.py` / access | `ui.tests.test_group_contract ui.tests.test_role_enforcement ui.tests.test_platform_modules` |
| PHV scanner | `ui.tests.test_phv ui.tests.test_phv_bugfixes ui.tests.test_phv_stock_totals` |
| ZARIA | `ui.tests.test_zaria ui.tests.test_zaria_api ui.tests.test_zaria_rag` |
| `views/__init__` wiring / URLs | `ui.tests.test_views_package` (+ `manage.py check`) |

## Configuration (environment variables)

Settings (`web/palletweb/settings.py`) are env-driven, and every variable is declared,
typed, and validated up-front in `web/palletweb/config.py` (`AppEnv`, **pydantic-settings**):
`settings.py` instantiates it once at import, so a missing/malformed var **fails fast at
startup** with an aggregated message instead of deep inside a request. `.env.example`
lists the deploy-critical ones. Key variables:
- `DJANGO_SECRET_KEY` (required when `DJANGO_DEBUG=false` — boot refuses the dev key),
  `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DB_PATH` / `MEDIA_ROOT` — SQLite DB and uploaded media (HU photos)
- `CELERY_BROKER_URL` (Redis) — recalculation tasks + driver-reminder beat job
- `SENTRY_DSN` / `SENTRY_ENVIRONMENT` — optional error tracking; `OTEL_ENABLED=1` opts
  into OpenTelemetry tracing
- `EMAIL_*`, `DEFAULT_FROM_EMAIL`, `WAREHOUSE_EMAIL` — SMTP for quote/readiness mail
  (falls back to a `mailto:`/Outlook flow when unconfigured)
- `TWILIO_*` — driver SMS (otherwise the link is shown for manual sending)
- `POWERBI_*` — pull warehouse stock straight from the Power BI / SAP BW cube (DAX
  executeQueries). Auth (in `ui/powerbi.py`): pasted `POWERBI_ACCESS_TOKEN`, a service
  principal (`POWERBI_CLIENT_SECRET`), or — when service principals are blocked — a
  delegated **device-code** login on Microsoft's public client (default
  `POWERBI_CLIENT_ID`); connect once via `manage.py powerbi_connect`, the MSAL refresh
  token is persisted in the `PowerBIToken` row and refreshed silently
- `GOOGLE_MAPS_API_KEY` — real road distances on the quote screen
- `SHIPMENT_ORIGIN_ADDRESS`, `COMPANY_NAME`, `SITE_BASE_URL` (production canonical domain is
  `https://groove.example.com`; `palviz.example.com` is kept as a transitional alias)
- `TWA_PACKAGE_NAME`, `TWA_SHA256_FINGERPRINT` — enables `assetlinks.json` for the APK
- `PALVIZ_API_TOKEN` — `X-API-Key` for the external HU-scanner REST API (`ui/api.py`,
  built on **django-ninja**; endpoints are rate-throttled, e.g. `AuthRateThrottle("120/m")`)
- `CSP_REPORT_ONLY` — CSP shipped report-only by default (see `core/middleware.py`); flip to
  `false` to enforce. `RequestIDMiddleware` mints/echoes an `X-Request-ID` per request

DB is **SQLite by default** (volume-mounted in Docker). Set `DATABASE_URL`
(`postgresql://user:pass@host:5432/palviz?sslmode=require`) to use **PostgreSQL** instead
(e.g. a managed cloud instance); it falls back to SQLite when unset, so local dev / CI need
no Postgres. `DB_SSLMODE` / `DB_CONN_MAX_AGE` tune the connection; migrate data with
`dumpdata` → `migrate` → `loaddata`. History is tracked on several models via
`django-simple-history`; brute-force login protection via `django-axes`.

## Conventions & gotchas

Structural conventions (line-limit, app boundaries, frozen contracts, migration
hygiene) → canon lives in `ARCHITECTURE.md` §Konwencje strukturalne, not here.
Below is the operational/runtime shortlist:

- **Everything is synchronous (WSGI).** No `async def` anywhere; prod runs gunicorn
  sync workers (`palletweb.wsgi`, `WEB_CONCURRENCY`). Long work goes to **Celery**
  (`tasks.py`), and long-lived responses (ZARIA SSE in `zaria_stream.py`, print jobs in
  `huctl/views/hu_print.py`) use sync `StreamingHttpResponse` generators — note each
  such stream ties up a whole gunicorn worker. Write new views sync; don't introduce
  `async def` views without switching to ASGI first.
- **After editing models or URLs, run `manage.py check`** (2 s, allowlisted) — it
  catches broken star-exports and import errors before the test suite does. Pre-commit
  now also runs `makemigrations --check` when `models*` files change.
- **Don't depend on third-party CDNs at runtime** — front-end libs are vendored via
  `fetch_vendor.sh` into `static/ui/vendor/`. WhiteNoise serves static; uploaded media
  is served from disk.
- **Celery:** heavy work (full recalculation) goes through tasks in `tasks.py`. In
  production a worker (`celery -A palletweb worker`) and beat (`celery -A palletweb beat`)
  are required; in DEBUG they run eagerly.
- **Lazy/optional imports:** OR-Tools, WeasyPrint (PDF), RapidFuzz (fuzzy REF search),
  structlog and OpenTelemetry are imported lazily so the core keeps working when
  they're absent.
- The standalone `palletizer` package must stay **framework-free** (no Django imports)
  so the CLI and tests keep working independently.

## Definition of Done (DoD)

Pętla dyscypliny, którą stosuj w **każdej** zmianie — także (a zwłaszcza) gdy prompt
użytkownika jest nieprecyzyjny. Pisany głosowo prompt bywa mętny; nie zgaduj po cichu.

1. **Diagnoza przed zmianą.** Przy „napraw X" bez jasnej przyczyny: najpierw zdiagnozuj,
   pokaż przyczynę, dopiero potem edytuj. Do orientacji „jak/gdzie" pytaj graf
   (`graphify query`) zamiast grep + czytanie całych plików — graf w `graphify-out/`
   jest już zbudowany, `query` zwraca skondensowaną odpowiedź bez wciągania plików do
   kontekstu (oszczędza tokeny).
2. **Nieprecyzyjny prompt → dopytaj albo nazwij założenia.** Jeśli brakuje **CO** /
   **GDZIE** (który ekran — Paletyzacja, widok 3D shipmentu, Kontrola HU…) / **NAPRAW vs
   ZDIAGNOZUJ**, zadaj jedno krótkie pytanie albo jawnie wypisz przyjęte założenia
   **przed** kodem. Nie edytuj 15 plików „na wszelki wypadek".
3. **Mały zakres.** Jedna zmiana = jeden branch `claude/**` + PR. Duży pomysł rozbij na
   kawałki (np. `to-tickets`), nie rób pięciu rzeczy naraz — to główne źródło regresji.
4. **Test = strażnik regresji.** Każdy bugfix i nietrywialna logika dostaje test.
   Uruchom `palletizer/tests` + `web/ui/tests` (patrz sekcja **Tests**) i **pokaż wynik**,
   zanim ogłosisz „gotowe".
5. **Review + ładny UI.** Zmiany UI: trzymaj Polish-first i świadomy design
   (skille frontend-design / ui-ux-pro-max), nie domyślne szablony. Przy większych
   zmianach zaproponuj `/code-review`.

**Nie ogłaszaj „zrobione", dopóki testy nie przeszły i zakres nie został zweryfikowany.**

## Git workflow

CI/CD is split across `.github/workflows/`:

- **`ci.yml`** — runs on **`pull_request` → `main`** (and `workflow_dispatch`). One `test`
  job on the self-hosted runner: 500-line gate, the palletizer unittest suite, then
  `web/manage.py check` + all four app test suites on PostgreSQL, the test-count guard, a
  star-import ruff check and `manage.py check --deploy` (ruff/mypy/pip-audit/bandit as full
  gates were dropped from the PR check on 2026-08-18; security scanners live in
  `security.yml`). Read the workflow for the current step list. `test` is the **required**
  status check on `main`.
- **`ci.yml` `auto-merge` job** — for a PR whose head branch is `claude/**`, once `test`
  passes it arms **GitHub-native auto-merge** (`gh pr merge --auto --merge`), so the PR
  merges **through the PR** (a normal merge commit, respecting branch protection — *not* a
  direct push or `-X theirs`). Needs repo Settings ▸ General ▸ "Allow auto-merge" on and
  `test` set as a required check. The job uses the `AUTOMERGE_PAT` secret (fine-grained PAT:
  contents+PR write) so the merge is a *real* push — without it the merge runs as
  `GITHUB_TOKEN` and GitHub suppresses the downstream `deploy.yml` trigger.
  Since #745 the job (1) updates a PR branch that is behind `main` instead of merging it,
  so the merge result is tested, and (2) does **not** arm auto-merge when the PR touches
  migrations, `core/roles.py`, `palletweb/settings.py|config.py`, `Dockerfile`,
  `docker-entrypoint.sh` or `.github/` (`.github/scripts/risky_paths.js`) — those PRs get a
  comment and are merged manually after review.
- **`deploy.yml`** — runs on **`push` → `main`** (i.e. right after the PR merges): fires the
  Coolify deploy webhook + a post-deploy smoke check. Auto-firing after a bot auto-merge
  requires `AUTOMERGE_PAT` (see above); otherwise trigger manually with
  `gh workflow run deploy.yml`.
- **`security.yml`** — fast scanners (gitleaks / Trivy / pip-audit / npm) on PRs + `main` +
  weekly. **`codeql.yml`** — heavier, on `main` pushes + weekly + on demand (not per PR).

**So the real path to prod:** push the `claude/**` branch **and open a PR into `main`** (a
bare push does nothing — CI is PR-triggered). Keep the suites green; the `claude/**` PR then
auto-merges and the merge to `main` deploys. Practically: `git push -u origin claude/<name>`
then `gh pr create --base main`. Treat a merged `claude/**` PR as a deploy to `main`.
