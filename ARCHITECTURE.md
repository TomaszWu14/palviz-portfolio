# GROOVE — architektura platformy

**GROOVE** to parasolowa platforma logistyczna (marka zastępuje wcześniejsze PalViz).
Wszystko żyje w **jednym repozytorium** (projekt Django `palletweb`, aplikacja `ui`).
Po zalogowaniu na `groove.example.com` użytkownik widzi **hub** z kafelkami modułów
(„zakładek"), do których ma dostęp; wchodzi w wybrany moduł i pracuje w jednej aplikacji.

> Świadoma decyzja: **bez mikroserwisów / osobnych repo.** Moduły to wewnętrzne obszary
> jednej aplikacji — prostsze w utrzymaniu (jeden deploy, jedna baza, jeden login).
> Ta i inne kluczowe decyzje (SAP tylko do odczytu, WSGI, CI, Kontrola HU…) są spisane
> w rejestrze ADR: [`docs/adr/README.md`](docs/adr/README.md).

## Marka i domena (env-driven)

- **Nazwa**: `APP_NAME` (domyślnie `GROOVE`) → `{{ app_name }}` w każdym szablonie przez
  `core.context_processors.branding`. Zmiana = jedna zmienna env (np. `APP_NAME=PalViz`).
- **Domena**: env w Coolify — `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`,
  `SITE_BASE_URL`. TWA/APK (`android/`) ma host w `twa-manifest.json`.
- Identyfikatory **techniczne** (`palletweb`, `ui`, `palletizer`, `palviz-three.js`)
  zostają — to nie marka.

## Hub i moduły („zakładki")

Rejestr: `web/ui/platform_modules.py` (`MODULES`, `modules_for(user)`). Hub
(`module_home` → `ui/home.html`) renderuje kafelki modułów dostępnych dla roli użytkownika;
przy jednym dostępnym module wchodzi od razu, operatorzy „control-only" idą prosto na skaner.

| Moduł | Wejście (`ui:<url_name>`) | Role |
|-------|---------------------------|------|
| **Data Center** — master data: opakowania, stock, klienci, produkty | `data_center` | Admin, Master Data |
| **Paletyzacja** — jak układać ładunek i dobierać opakowanie | `planner_calc_index` | Admin, MD, Transport |
| **Wycena przesyłek** — objętość, ładunek, przewoźnicy, wyceny | `planner_shipments` | Admin, Transport |
| **Magazyn 3D** — lokalizacje, widok 3D, kompletacja i analiza | `warehouse_map` | Admin, MD, Transport, Podgląd |
| **Kontrola HU** — skaner, kontrola zawartości, KPI i raporty | `hu_control_hub` | Admin, Kontrola HU, Lider |
| **Wydruk HU** — etykiety HU (Zebra/ZPL), druk sekwencyjny per projekt | `hu_print_home` | Admin, Magazyn |
| **Zadania i powiadomienia** — zadania zespołu i alerty o niezgodnościach stocku | `tasks_home` | Admin, Master Data |
| **Baza klientów** — klienci / odbiorcy i ich wymagania dostaw | `planner_customers` | Admin, MD, Transport, Obsługa klienta |
| **ZARIA** — asystent AI, czat z modelami LLM | `zaria_home` | Admin, MD, Transport, Kontrola HU, Lider, Podgląd, Magazyn |
| **Wysyłka UKRAINA** — monitoring wskazanych partii: ACME vs DLT | `ukraine_home` | Admin, Transport, MD |
| **MATinfo** — podgląd materiału: szt → OPZ → karton → ładunek, przeliczniki, wagi, zgłoszenia | `phv_home` | Admin, MD, Magazyn, Kontrola HU, Lider |
| **Optymalizacja kartonów** — zarządzanie wypełnieniem ładunku: zgłoszenia ze skanera, pilność, warianty kartonów | `carton_opt_inbox` | Admin, Optymalizacja kartonów, MD |

**Silnik niezgodności** (`ui/notifications.py`, `run_stock_discrepancy_checks`): po
odświeżeniu stocku (import HU / Power BI) skanuje HU i tworzy zadania (z deduplikacją) +
powiadomienia dla Master Data/Admin. Reguły: brak danych opakowania, przeterminowane/krótka
data, błędna lokalizacja (kod spoza master), objętość > pojemności lokalizacji.

### Dodanie / zmiana modułu

1. Dodaj widoki w `ui/views/<feature>.py` i URL-e w `ui/urls.py` (namespace `ui:`),
   strzeżone rolą z `roles.py`.
2. Dopisz/popraw wpis `Module(...)` w `platform_modules.py` — `key`, `name`, opis,
   `url_name` (wejście), `icon`, `color`, role. Kafelek pojawia się w hubie automatycznie.

### Data Center

Moduł `data_center` (`ui/views/data_center.py` → `ui/data_center.html`) to centralny hub
master data: kafelki + liczniki linkujące do ekranów produktów, kartonów/opakowań, kategorii,
klientów, stocku (zaimportowane HU), materiałów referencyjnych oraz szablonów/importu Excel.

## Konwencje strukturalne

Kanoniczny opis — jedno źródło prawdy; `CLAUDE.md` §Conventions & gotchas linkuje
tutaj zamiast duplikować treść.

**Limit ~500 linii/plik.** Cały kod był świadomie podzielony do tego limitu
(PR #550–#556). Wzorzec podziału: sibling module + re-export z `__init__`/facada
pakietu — nie rozrastać istniejącego pliku ponad limit, tylko dzielić. Wymuszane
narzędziowo: `web/scripts/file_size_check.py` (`LIMIT = 500`; wyjątki: katalogi
`migrations`/`vendor` i dokładnie `palletweb/settings.py`), krok w CI
(`.github/workflows/ci.yml`, "File size limit gate") i w pre-commit.

**Granice aplikacji.** `ui` vs `wh3d`/`huctl`/`transport` — brak cross-importów
między leaf appami; strażnik `web/ui/tests/test_module_boundaries.py` (Faza 2)
pilnuje tego testem.

**Wspólny rdzeń `core` (Faza 8).** Współdzielona infrastruktura (nie domena)
mieszka w osobnej appce `core` **bez modeli i migracji**: `roles.py` (słownik ról +
kontrakt SSO), `middleware.py`, `context_processors.py`, `platform_modules.py`
(rejestr hubu), `health_urls.py`. Podział intencyjny: **`core` = shared kernel,
`ui`/`wh3d`/`huctl`/`transport` = domeny.** Stare ścieżki importu (`ui.roles`,
`ui.platform_modules`) działają dalej przez cienkie shimy re-eksportujące w `ui/`
(martwe shimy `ui.middleware`/`ui.context_processors`/`ui.health_urls` usunięte — ARCH-004) — kontrakt SSO (`oidc.py`)
i 160+ importerów bez zmian (`test_group_contract` zielony). Modele zostają w `ui`,
więc te pliki importują `ui.models` **funkcyjnie (w runtime)**, nie na poziomie
modułu — brak cyklu przy ładowaniu. Realny dom to `core.*`; do niego kieruje
`settings.py` (MIDDLEWARE, context processors) i `urls.py` (health). Edytuj kod
w `core.*`, nie w shimach.

**Podział modeli i widoków po domenach.** `ui/models/` to pakiet re-eksportowany
przez `__init__.py` (catalog, packaging, warehouse, customers, …) — nowy model
trafia do właściwego modułu domenowego, importy `from ui.models import X` działają
nadal. `views/` to pakiet, każda feature ma swój moduł (<500 linii), `views/__init__.py`
re-eksportuje przez `from .x import *`; widok jest osiągalny tylko gdy jest
wyeksportowany ORAZ wpięty w `urls.py` (namespace `ui:`).

**URL-e per app (Faza 5).** Nowy widok → wpis w `urls.py` appki, w której żyje
funkcja widoku (`wh3d`/`huctl`/`transport`), nie w `ui/urls.py`. `ui/urls.py`
włącza leaf-urlconfy BEZ prefiksu; żaden z nich nie deklaruje własnego
`app_name` — wszystkie nazwy scalają się do jednego namespace ui:, więc
`reverse("ui:<name>")` i `{% url %}` działają identycznie niezależnie od tego,
w której appce żyje widok. Przykład: `path("", include("wh3d.urls"))`.
Strażnik regresji: `web/ui/tests/test_urls_snapshot.py`.

**Wzorzec nazw plików modeli w appkach.** Gdy modele appki rosną poza jedną
domenę: pakiet `models/__init__.py` re-eksportujący `from .domena import *`
(`ui`) ALBO fasada `models.py` (kilka linii, `from .models_domena import *`) +
pliki `models_<domena>.py` obok (`huctl`: `models_hu.py`/`models_control.py`/
`models_print.py`; `transport`: `models_quoting.py`/`models_shipment.py`) — obie
formy są zgodne z konwencją, wybór zależy od tego, czy app zaczynał jako pakiet
czy jako pojedynczy plik. Mały, jednoplikowy `models.py` (wh3d) jest zgodny z
konwencją, dopóki app nie urośnie ponad limit ~500 linii/plik.

**Higiena migracji.** `makemigrations` zawsze przy zmianie modeli; nigdy nie
edytować zaaplikowanej migracji — dodać nową. Wymuszane pre-commit
(`makemigrations --check`) oraz testem-strażnikiem `web/ui/tests/test_migrations_check.py`
(`makemigrations --check --dry-run` dla `ui`/`wh3d`/`huctl`/`transport`, biegnie
w ramach `manage.py test ui.tests`).

**Migracje expand → contract (BUILD-001).** `docker-entrypoint.sh` odpala `migrate` przy
starcie nowego kontenera, gdy stary jeszcze obsługuje ruch — więc migracja musi być
zgodna wstecz z kodem poprzedniej wersji. Zasada: najpierw **expand** (dodaj kolumnę /
model, kod przestaje używać starego), dopiero w **kolejnym** deployu **contract**
(`RemoveField`, `DeleteModel`, `RenameField`, `RenameModel`). Nowa migracja z operacją
contract musi mieć w treści komentarz `# contract:` z numerem PR/deployu, który już
usunął użycia — pilnuje tego `web/ui/tests/test_migration_contract.py`. Przy >1 replice
przenieść `migrate` do kroku pre-deploy w Coolify zamiast entrypointu.

**Zamrożone kontrakty** — refaktor identyfikatorów Python TAK, wartości-kontrakty
NIE:
- Nazwy grup ról w `web/ui/roles.py` (`"Administratorzy"`, `"Master Data"`, …) —
  to jednocześnie wiersze `auth_group` w DB i słownik, na który SSO (`oidc.py`)
  mapuje claimy IdP; pinowane testem `test_group_contract.py`.
- Alias `Z129` w `palletizer/config.py` — legacy nazwa presetu palety EU 120×80,
  trzymana żeby stare rekordy DB nadal się rozwiązywały.
- Techniczne nazwy wewnętrzne: projekt `palletweb`, app label `ui`, pakiet
  `palletizer` — to nie marka (GROOVE/PalViz), zostają bez zmian.

## Zasada przewodnia

Trzymaj funkcję na właściwej głębokości: nowy obszar = moduł w tym repo (wpis w `MODULES`).
Jeden brand, jedno repo, jeden login — moduły są zakładkami tej samej aplikacji.

## Mapa dokumentacji

Gdzie żyje jaki typ dokumentu (ADR, spec, mapa procesu, archiwum, …) — patrz
[`docs/README.md`](docs/README.md).
