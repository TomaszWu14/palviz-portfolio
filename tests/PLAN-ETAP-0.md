# System testów regresji GROOVE — Etap 0: analiza i plan

Stan na 2026-09-26, `main` @ 1a0b6d9. Etap 0 nie dodaje testów — tylko inwentarz, macierze
(`tests/permissions.yaml`, `tests/ui-permissions.yaml`), plan i pytania. **Czeka na akceptację.**

## 1. Przełożenie wymagań na realia PalViz/GROOVE

Wymagania pisane były pod aplikację FastAPI + React (kontenery, SafeCube). GROOVE to Django +
szablony, więc część punktów ma odpowiednik, część nie dotyczy:

| W wymaganiach | W GROOVE | Uwagi |
|---|---|---|
| role + TDD scopingu w `deps.py` | 9 grup (`core/roles.py`) + `module_required` + `UserModuleAccess` | istnieją `test_role_enforcement`, `test_group_contract`, `test_platform_modules` — rozbudowa, nie duplikat |
| spółki (IDOR między spółkami) | **brak tenantów** — jedna organizacja | odpowiednik: obiekty per użytkownik (ZARIA prywatne, powiadomienia, wątki, zadania, rezerwacje HU) + warianty `GROOVE_VARIANT` — **P-4** |
| kontenery, statusy 1–8, pasek postępu | statusy Shipment / kontroli HU / zadań / zgłoszeń | przejścia per rola — etap 2 |
| ISO 6346 (cyfra kontrolna) | cyfry kontrolne GS1: EAN-13, SSCC (HU), kody lokalizacji EWM | formularz, API, import |
| magazyny DLT / ACME / bez magazynu | moduł „Wysyłka UKRAINA” (`UKRAINE_WAREHOUSE_ACME/DLT`) + typy magazynu EWM | liczniki zgodne z rekordami |
| „obserwowane / specjalna troska” | VIP HU, reguły pakowania klient×indeks, pilne komunikaty ack | **P-5** |
| kalendarz rok→miesiąc→dzień, `/dzis` | brak | pomijamy (albo: kalendarz transportu — P-5) |
| SafeCube | Power BI (DAX/MSAL), Google Maps, Twilio, SMTP, OIDC, ZARIA LLM (Anthropic/OpenAI/Azure/Ollama), NBP, n8n, Teams, Master Data API | wszystkie mockowane, sieć zablokowana |
| React: komponenty, eslint, tsc, `dist` | 204 szablony + 4 moduły JS (three.js odtwarzacz, mapy) + vendor; `web/package.json` | eslint na JS; zamiast „dist” — `collectstatic` + manifest |
| PostgreSQL „jak produkcja” | **Postgres 17** (Coolify `c32q…`); CI dziś: kontener `palviz-ci-postgres` (15) | docker-compose.test.yml z `postgres:17` |

## 2. Inwentarz

- **Istniejące testy:** `ui` 120 modułów / 763 metody, `wh3d` 39 / 327, `huctl` 93 / 504,
  `transport` 21 / 188, `palletizer` 6 / 32 → **≈1 814 testów** (CI: 1 782 w Django + palletizer),
  runner `manage.py test --parallel`, strażnik minimalnej liczby testów (TEST-02), snapshot URL-i.
  Uprawnienia dziś: `test_role_enforcement` (10 ręcznych przypadków + lista anonimowa 15 tras),
  `test_group_contract`, `test_nav_groups`, `test_platform_modules`, `test_csrf` (1 przypadek).
  **Luka:** brak automatycznej macierzy — 402 z 417 tras nie ma testu uprawnień per rola.
- **Role (9 + superuser):** Administratorzy, Master Data, Transport, Kontrola HU, Lider kontroli,
  Podgląd, Obsługa klienta, Magazyn, Optymalizacja kartonów. Persony testowe dodatkowo: anon,
  nieaktywny, bez roli, zablokowany przez axes.
- **Trasy:** 687 (417 aplikacyjnych + 270 panelu `/admin/`). Strażnicy tras aplikacyjnych:
  `role_required` 277 · `module_required` 35 · tylko login 73 · API ninja 10 · bez dekoratora 22
  (14 publicznych z założenia, 4 z tokenem, 3 sprawdzają w środku, **1 błąd — B-001**).
  Metody: 145 tras tylko POST (`require_POST`), reszta GET/POST w jednym widoku.
- **Formularze:** 20 klas Django (`forms*.py`) + formularze ad hoc w widokach (POST bez klasy).
- **Importy:** 31 modułów widoków / 37 miejsc uploadu (Excel/CSV: produkty, MARM, dane materiałowe
  SAP, kartony, lokalizacje, snapshot, master lokalizacji, układ, heatmapa, zadania EWM, HU/stock,
  użytkownicy, klienci, wyceny, Ukraina, artwork…).
- **Eksporty:** 23 moduły (XLSX, CSV, PDF WeasyPrint, JSON sceny Blendera).
- **Zadania w tle:** 12 zadań Celery; beat: przypomnienia kierowców (5 min), zadania cykliczne (6 h),
  retencja ZARIA i zdjęć HU (doba), KPI transportu (1 h), opcjonalnie raport HU, backup, Power BI.
- **Integracje zewnętrzne:** Power BI, Google Maps, Twilio, SMTP/e-mail, OIDC/SSO, LLM (ZARIA),
  NBP (kursy), n8n (zdarzenia), Teams (webhook), Master Data API, Sentry, OpenTelemetry.
- **Zmienne środowiskowe:** 113 w `palletweb/config.py` (pydantic, fail-fast).

## 3. Znalezione już w etapie 0

Osiem wpisów w `BUGS-FOUND.md`: 3 potwierdzone próbą (B-001 🔴 snapshot bez logowania, B-002 🟠 zdjęcia
HU/MATinfo bez logowania, B-003 🟠 przeliczenie instrukcji dla każdej roli) + 5 drobnych niespójności UI z
analizy statycznej (B-004…B-008, do potwierdzenia testem w etapie 4). Nie naprawiam bez zgody.

## 4. Kluczowe scenariusze E2E (propozycja, 15)

1. Logowanie każdą rolą → hub pokazuje dokładnie kafelki z `ui-permissions.yaml`; operator
   kontroli ląduje w skanerze.
2. Master Data: dodanie produktu + kartonu → instrukcja paletyzacji → widok 2D/3D → eksport Excel.
3. Import MARM / danych materiałowych SAP przez UI → raport → dane widoczne w katalogu.
4. Paletyzacja: kalkulator (zapis wariantu) → ręczna edycja układu → zapis i odświeżenie.
5. Transport: nowy shipment → pakowanie 3D → wycena przewoźników → mail/link wyceny.
6. Kierowca: formularz z tokenem → potwierdzenie odbioru (publiczny link).
7. Kontrola HU: skan HU → liczenie → rozbieżność → eskalacja do lidera → zamknięcie.
8. Lider: kolejka, rezerwacje (soft-assign), KPI per strefa.
9. Wydruk HU (Magazyn): projekt → druk sekwencyjny (mock drukarki).
10. MATinfo (skaner): podgląd materiału → zgłoszenie ze zdjęciem → „Moje zgłoszenia”.
11. Magazyn 3D: snapshot zajętości → mapa 3D → karta lokalizacji → „gdzie jest”.
12. Zadania EWM: import → animacja wózków → dzień projektowy (P90/P95/P99).
13. Zadania i komunikaty: zadanie przypisane → powiadomienie → pilny komunikat z potwierdzeniem.
14. ZARIA: prywatny czat (izolacja per użytkownik), budżet, eksport rozmowy.
15. Administracja: użytkownik → rola → nadpisanie modułu → audyt dostępu; SSO blokuje edycję ról.

## 5. Mapa ryzyk (co najbardziej boli, gdy się zepsuje)

| Ryzyko | Skutek | Prawd. | Priorytet testów |
|---|---|---|---|
| Uprawnienia (anon/rola widzi lub zmienia za dużo) | wyciek danych, zmiany bez autoryzacji — **już 3 przypadki** | wysokie | macierz + IDOR per obiekt |
| Silnik paletyzacji / pakowania shipmentu | złe palety, złe wyceny, reklamacje | średnie | istniejące testy + E2E 2, 4, 5 |
| Kontrola HU (bramki skanu, statusy, KPI) | błędne wydania, brak śladu audytowego | średnie | przejścia statusów per rola, E2E 7–8 |
| Importy z SAP/EWM (formaty, kodowanie, częściowy zapis) | cicho błędne dane podstawowe | wysokie | pliki złośliwe/brzegowe, raport per wiersz |
| Migracje (192 w `ui`) i Postgres 17 | nieudany deploy | średnie | migracje od zera + od kopii schematu prod |
| Integracje (Power BI, e-mail, SMS, LLM) | brak stanów, maili, koszty LLM | średnie | mocki + blokada sieci |
| Wydajność widoków 3D / list (N+1) | wolna aplikacja na hali | średnie | limity zapytań, obciążenie |
| Tokeny publiczne (wyceny, kierowcy) | dostęp z zewnątrz | niskie | zgadywanie/wygaśnięcie tokenu |

## 6. Układ katalogów (propozycja)

- `tests/` (root): specyfikacje (`permissions.yaml`, `ui-permissions.yaml`), `e2e/` (Playwright),
  `perf/` (Locust/k6), `tools/` (inwentarz, generatory), `README-TESTS.md`, `TEST-MATRIX.md`.
- Backend: dalej w `web/*/tests` (konwencja repo). Nowe suity uruchamiane przez **pytest +
  pytest-django** (już w `requirements-dev.txt`, zgodne z istniejącymi testami unittest).
- `make test` / `make test-full` / `make test-role ROLE=…` (Makefile + odpowiednik `.bat`).

## 7. Pytania do decyzji (oznaczone „?” w macierzach)

- **P-1 (B-001, B-003):** jakie są docelowe uprawnienia `/magazyn/<pk>/` i przeliczeń instrukcji?
  Proponuję: snapshot = moduł „magazyn” (Admin/MD/Transport/Podgląd), przeliczenia = Admin/MD.
- **P-2:** 73 trasy „tylko login” (m.in. katalog produktów, instrukcje, kartony, lokalizacje,
  modele magazynu, raporty) są dziś widoczne dla **każdej** zalogowanej roli — także wąskich
  (Obsługa klienta, Magazyn, Kontrola HU, Lider, Optymalizacja). Zostawiamy (odczyt dla wszystkich)
  czy zawężamy? W macierzy wąskie role mają tu „?”.
- **P-3 (B-002):** które katalogi `/media/` mają wymagać logowania? Proponuję: `quality/`,
  `hu_control/`, `phv/`, `ewm_tasks/`; publiczne zostają grafiki, modele 3D i załączniki wycen.
- **P-4:** separacja „spółek” — potwierdź, że testujemy izolację obiektów per użytkownik
  (ZARIA, powiadomienia, wątki, zadania, rezerwacje HU) zamiast spółek, których w GROOVE nie ma.
- **P-5:** odpowiedniki „obserwowane / specjalna troska / kalendarz” — czy chodzi o VIP HU,
  reguły pakowania klienta i pilne komunikaty? Czy pomijamy?
- **P-6:** CI działa na jednym self-hosted runnerze (`hetzner-gha-palviz`, bez Dockera, ten sam
  host co produkcja). Pełny pipeline (Playwright × przeglądarki, obciążenie, Trivy) — uruchamiać
  tam nocą czy na hostowanych runnerach GitHuba (płatne minuty)?

## 8. Decyzje (2026-09-26 — Etap 0 zaakceptowany, propozycje przyjęte jako domyślne, do zmiany)

- **P-1:** oczekiwane — snapshot `/magazyn/<pk>/` jak moduł „magazyn”; przeliczenia instrukcji Admin/MD.
  Macierz zapisze docelowe zachowanie; test oznaczy B-001/B-003 jako `expectedFailure` do naprawy.
- **P-2:** trasy „tylko login” zostają dostępne dla wszystkich zalogowanych (stan obecny utrwalony).
- **P-3:** logowanie wymagane dla `quality/`, `hu_control/`, `phv/`, `ewm_tasks/` (B-002 jak wyżej).
- **P-4:** zamiast spółek — izolacja obiektów per użytkownik (ZARIA, powiadomienia, wątki, zadania, rezerwacje HU).
- **P-5:** „obserwowane / specjalna troska / kalendarz” — pominięte.
- **P-6:** pipeline nocny na self-hosted runnerze (poza godzinami pracy).
- Naprawy B-001…B-008 — **tylko po osobnej zgodzie**.
