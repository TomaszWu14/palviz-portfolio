# BUGS-FOUND — błędy aplikacji wykryte przez system testów

Błędy w samej aplikacji GROOVE znalezione podczas budowy testów regresji. **Nie są naprawiane
bez zgody właściciela** — każdy wpis czeka na decyzję. B-001…B-011 naprawione za zgodą z 2026-09-26
(zachowanie wg decyzji P-1…P-3); każdy ma test regresji wskazany w PR. Waga: 🔴 wysoka · 🟠 średnia · 🟡 niska.

| ID | Waga | Obszar | Tytuł | Status |
|---|---|---|---|---|
| B-001 | 🔴 | Uprawnienia | Widok 3D snapshotu zajętości dostępny bez logowania | ✅ naprawiony (PR #694) |
| B-002 | 🟠 | Uprawnienia / media | Zdjęcia kontroli HU i zgłoszeń MATinfo pobieralne bez logowania | ✅ naprawiony (PR #694) |
| B-003 | 🟠 | Uprawnienia | Każdy zalogowany (także „Podgląd”, bez roli) uruchamia przeliczenie wszystkich instrukcji | ✅ naprawiony (PR #694) |
| B-004 | 🟡 | UI / uprawnienia | Listy danych podstawowych pokazują przyciski zapisu 7 rolom, które dostają 403 | ✅ naprawiony (PR #697) |
| B-005 | 🟡 | UI | Link „Django Admin” widoczny dla każdego (panel wpuszcza tylko `is_staff`) | ✅ naprawiony (PR #697) |
| B-006 | 🟡 | UI | „Mapa magazynu 3D” na stronie stanów prowadzi Kontrolę HU i Lidera do 403 | ✅ naprawiony (PR #697) |
| B-007 | 🟡 | UI / uprawnienia | Reguły pakowania klienta ukryte przed Administratorami (widok je przyjmuje) | ✅ naprawiony (PR #697) |
| B-008 | 🟡 | UI / uprawnienia | Flagi szablonów ignorują indywidualne nadpisania modułów (`UserModuleAccess`) | ✅ naprawiony (PR #697) |
| B-009 | 🟠 | Bezpieczeństwo / dostępność | Blokada django-axes po samym IP — 5 błędnych logowań blokuje wszystkich za tym samym adresem (proxy Coolify/NAT hali) | ✅ naprawiony (PR #695) |
| B-010 | 🟠 | ZARIA / MATinfo | Dostawcy Ollama / OpenAI / Azure zawsze zwracają błąd — brak pakietu `openai` w `requirements.txt` | ✅ naprawiony (PR #696) |
| B-011 | 🟡 | Wydajność / Transport | Awaria NBP nie jest zapamiętywana — przy niedostępnym API każdy widok shipmentów/wycen czeka do 4 s na kurs | ✅ naprawiony (PR #696) |
| B-012 | 🟠 | Komunikator / Kontrola HU | Panel nadzoru wiadomości (`/wiadomosci/panel/`) — błąd 500, gdy istnieje wątek systemowy bez autora (także widok wątku i szufladka) | ✅ naprawiony (PR #713) |
| B-013 | 🟡 | Wydruk HU | Dziennik wydruku (`/hu-print/log/`) — błąd 500, gdy autor wydruku został usunięty | ✅ naprawiony (PR #714) |
| B-014 | 🟠 | Magazyn / analityka | Kolory `var(--…)` przekazane do canvas / ECharts / Plotly — heatmapa maluje puste lokalizacje kolorem sąsiada, wykresy analityki bez kolorów | ✅ naprawiony (PR #722) |
| B-015 | 🟡 | Zadania / dostępność | Link zadania bez „źródła” ma jako treść samą strzałkę „→” (brak nazwy dostępnej, cel 10 px) | ✅ naprawiony (PR #728) |

---

## B-001 🔴 Widok 3D snapshotu zajętości dostępny bez logowania

- **Status:** ✅ naprawiony w PR #694 (2026-09-26).

- **Trasa:** `ui:warehouse_map_detail` → `GET /magazyn/<pk>/` (także `?fmt=json`)
- **Opis:** widok `wh3d/views/warehouse_map_detail.py:46` nie ma żadnego strażnika (dekoratora ani
  sprawdzenia w środku), a GROOVE nie ma globalnego wymuszania logowania. Anonim dostaje stronę
  i JSON z kodami lokalizacji, zajętością i blokadami magazynu.
- **Kroki:** wyloguj się → otwórz `https://groove.example.com/magazyn/6/?fmt=json`.
- **Oczekiwane:** 302 na `/login/` dla anonima; dla zalogowanych — jak moduł „magazyn”
  (Admin / Master Data / Transport / Podgląd) — do potwierdzenia (P-1).
- **Wykrył:** `tests/tools/inventory_routes.py` (guard „none”) + próba anonimowa
  (`anon HTML -> 200`, `anon JSON -> 200 {"locs": [...]}`); w etapie 2: test macierzy uprawnień.

## B-002 🟠 Zdjęcia kontroli HU i zgłoszeń MATinfo pobieralne bez logowania

- **Status:** ✅ naprawiony w PR #694 (2026-09-26).

- **Trasa:** `media_serve` (`ui/views/misc.py:28`) → `GET /media/hu_control/…`, `/media/phv/…`
- **Opis:** `media_serve` wymaga logowania tylko dla `quality/`. Zdjęcia dowodowe kontroli HU
  (`HUControlPhoto`, `huctl/models_control.py:247`, `upload_to="hu_control/%Y/%m/"`) oraz zdjęcia
  zgłoszeń opakowań/lokalizacji z MATinfo (`phv/%Y/%m/`, `phv/loc/%Y/%m/`) są serwowane anonimom.
  Ścieżki mają datę i nazwę pliku z urządzenia — przewidywalne.
- **Kroki:** wyloguj się → `GET /media/hu_control/<rok>/<mies>/<plik>.jpg` → 200.
- **Oczekiwane:** 302 na `/login/` jak dla `quality/` (zakres katalogów do potwierdzenia — P-3).
- **Wykrył:** przegląd `upload_to` × `media_serve` + próba anonimowa
  (`hu_control → 200`, `phv → 200`, `quality → 302`).

## B-003 🟠 Przeliczenie wszystkich instrukcji dostępne dla każdego zalogowanego

- **Status:** ✅ naprawiony w PR #694 (2026-09-26).

- **Trasy:** `ui:recalculate_all` → `POST /planner/tasks/recalculate-all/`;
  `ui:planner_instruction_recalculate_async` → `POST /planner/instructions/<pk>/recalculate/`
- **Opis:** oba widoki (`ui/views/calc.py:366-381`) mają tylko `_planner` (login). Zapis instrukcji
  to w `MODULE_ROLES` „instructions_write” = Admin + Master Data. Użytkownik „Podgląd”,
  „Obsługa klienta” albo bez roli może uruchomić ciężkie przeliczenie całej bazy instrukcji
  (obciążenie workera, nadpisanie wyników). CSRF chroni przed atakiem z zewnątrz, nie przed
  zalogowanym użytkownikiem.
- **Kroki:** zaloguj się jako użytkownik z rolą „Podgląd” → `POST /planner/tasks/recalculate-all/`
  (z tokenem CSRF) → `200 {"task_id": …}`, zadanie wykonane.
- **Oczekiwane:** 403 dla ról spoza Admin / Master Data (do potwierdzenia — P-1).
- **Wykrył:** `tests/tools/inventory_routes.py` (POST z guard „login”) + próba dla ról
  Podgląd / Obsługa klienta / bez roli (wszystkie `200`).

---

Wpisy B-004…B-008 wykryła **analiza statyczna** (szablony `{% if flaga %}` vs dekoratory widoków) w
etapie 0; zostaną potwierdzone testem widoczności UI (`tests/ui-permissions.yaml`) w etapie 4.

## B-004 🟡 Przyciski zapisu na listach danych podstawowych widoczne dla wszystkich ról

- **Status:** ✅ naprawiony w PR #697 (2026-09-26).
- **Opis:** listy są `@_planner` (każdy zalogowany), a akcje dodaj/edytuj/usuń/import — `@_md_role`
  (Admin, MD). Pozostałe 7 ról widzi przyciski i dostaje 403.
- **Miejsca:** `planner/products.html:46,54,83,215,218` · `cartons.html:29,45,108,111` ·
  `instructions.html:25,86,89` · `inner_packs.html:21,67,70` · `categories.html:21,56,59` ·
  `locations.html:27,98,101` · `wh3d/.../warehouse_model/list.html:34,82`.
- **Oczekiwane:** przyciski za `{% if can_write_products %}` (wzorzec już użyty w szablonach wh3d).

## B-005 🟡 „Django Admin” w menu użytkownika dla każdego

- **Status:** ✅ naprawiony w PR #697 (2026-09-26).
- **Miejsce:** `ui/templates/ui/base.html:137` — bez warunku; `/admin/` wpuszcza tylko `is_staff`.
- **Oczekiwane:** link tylko dla `user.is_staff`.

## B-006 🟡 Link do mapy 3D ze strony stanów kończy się 403 dla Kontroli HU i Lidera

- **Status:** ✅ naprawiony w PR #697 (2026-09-26).
- **Miejsce:** `stock.html:20` (bezwarunkowy link) — strona stanów wpuszcza Admin/MD/KHU/Lid
  (`hu_stock.py:15`), mapa wymaga modułu „magazyn” (`warehouse_map_core.py:20`).

## B-007 🟡 Reguły pakowania klienta ukryte przed Administratorami

- **Status:** ✅ naprawiony w PR #697 (2026-09-26).
- **Miejsce:** `can_edit_rules` = MD lub superuser (`transport/views/customers.py:155`), a widoki
  `customer_rule_add/delete` przyjmują Admin + MD (`:159`, `:188`) — admin z grupy nie widzi kontrolek.

## B-008 🟡 Flagi w szablonach nie uwzględniają nadpisań modułów per użytkownik

- **Status:** ✅ naprawiony w PR #697 (2026-09-26).
- **Przykład:** zakładka „MATinfo” w skanerze (`scanner/base.html:346`, flaga `can_open_matinfo` z ról)
  vs `phv_home` z `@module_required("phv")` (z nadpisaniami). Odebrany dostęp → zakładka widoczna, 403;
  nadany dostęp bez roli → brak zakładki.

---

Wpisy B-009…B-011 wykryła **infrastruktura testów (etap 1)** — persony (`web/testkit/personas.py`)
i testy kontraktu integracji (`web/ui/tests/test_integrations_contract.py`).

## B-009 🟠 Blokada django-axes po samym IP — jedna osoba blokuje wszystkich

- **Status:** ✅ naprawiony w PR #695 (2026-09-26).

- **Miejsce:** `palletweb/settings.py:260` (`AXES_FAILURE_LIMIT=5`, `AXES_COOLOFF_TIME=1`), brak
  `AXES_LOCKOUT_PARAMETERS` → domyślnie django-axes 8 blokuje **wyłącznie po adresie IP**. Brak
  też `django-ipware` / `AXES_CLIENT_IP_CALLABLE` / obsługi `X-Forwarded-For`, więc adresem jest
  `REMOTE_ADDR` — na produkcji za reverse proxy Coolify (Traefik) to adres proxy, wspólny dla
  wszystkich użytkowników (a na hali i tak wszyscy wychodzą przez jeden NAT).
- **Skutek:** 5 nieudanych logowań kogokolwiek (literówka operatora skanera, zgadywanie hasła
  z zewnątrz) blokuje logowanie formularzem **wszystkim** na 1 godzinę. Łatwy DoS logowania.
- **Kroki:** 5× złe hasło dla dowolnego loginu → poprawne dane innego użytkownika z tego samego
  adresu → `429` (strona blokady axes).
- **Oczekiwane:** blokada per para `[username, ip_address]` (`AXES_LOCKOUT_PARAMETERS`) oraz
  prawdziwy adres klienta zza zaufanego proxy (`AXES_IPWARE_PROXY_COUNT` / `AXES_CLIENT_IP_CALLABLE`).
  Do decyzji — nie naprawiam bez zgody.
- **Wykrył:** `ui.tests.test_testkit_seed.PersonaBehaviourTests.test_axes_lock_of_one_user_does_not_block_others_from_same_ip`
  (`expectedFailure`, dziś `429`).

## B-010 🟠 ZARIA: Ollama / OpenAI / Azure OpenAI nie mogą działać — brak pakietu `openai`

- **Status:** ✅ naprawiony w PR #696 (2026-09-26).

- **Miejsce:** `ui/zaria_llm.py:134` i `:214` (`import openai` w `_complete_openai` / strumieniu);
  `requirements.txt` ma tylko `anthropic`, obraz Docker instaluje wyłącznie `requirements.txt`.
- **Skutek:** każde wywołanie modelu z `provider` ∈ {`ollama`, `openai`, `azure_openai`} kończy się
  `ImportError`, łapanym jako ogólny „Błąd usługi AI. Spróbuj ponownie później.” — także
  **asystent MATinfo** (`phv_assistant`, zawsze Ollama) zwróci `502`, gdy tylko
  `ZARIA_OLLAMA_BASE_URL` zostanie ustawione. Health-check Ollamy (urllib) pokaże „dostępny”,
  więc przyczyna jest niewidoczna dla administratora.
- **Kroki:** ustaw `ZARIA_OLLAMA_BASE_URL` na działającą Ollamę → MATinfo → „Zapytaj asystenta”
  → `502 {"error": "Błąd usługi AI…"}`.
- **Oczekiwane:** `openai` (wersja zgodna z SDK) w `requirements.txt` — decyzja właściciela.
- **Wykrył:** `ui.tests.test_integrations_contract.ZariaLLMTests.test_ollama_answers_when_server_ok`
  (`expectedFailure`).

## B-011 🟡 Awaria NBP nie jest zapamiętywana — każdy render czeka ponownie

- **Status:** ✅ naprawiony w PR #696 (2026-09-26).

- **Miejsce:** `ui/nbp.py:35-47` — sukces trafia do cache na 6 h, porażka (timeout/HTTP/zły format)
  nie jest zapisywana wcale. `transport/views/shipments_core.py:39` woła `get_rate("EUR")` przy
  każdym renderze danych shipmentu (lista/planer), `quotes.py:34` — po razie na walutę ofert.
- **Skutek:** gdy api.nbp.pl nie odpowiada, każde otwarcie ekranu shipmentu/wycen czeka do
  `_HTTP_TIMEOUT` = 4 s na walutę, a worker gunicorna (synchroniczny) stoi. Przy kilku użytkownikach
  wyczerpuje to pulę workerów.
- **Kroki:** zablokuj api.nbp.pl (albo NBP ma awarię) → dwukrotnie otwórz planer shipmentu → dwa
  pełne timeouty.
- **Oczekiwane:** krótkie zapamiętanie porażki (np. 5–10 min „brak kursu”) — decyzja właściciela.
- **Wykrył:** blokada sieci testkit (`[testkit] zablokowano połączenie sieciowe: 198.51.100.37:443`
  z `transport.tests.test_shipment_planner_ui`, `test_shipment_calc_more`, `test_import_status` —
  te testy **wołały prawdziwe API NBP z CI**) + `ui.tests.test_integrations_contract.BestEffortIntegrationTests.test_nbp_outage_not_retried_on_every_render`
  (`expectedFailure`).

---

## B-012 🟠 Panel nadzoru wiadomości — 500 przy wątku bez autora

- **Status:** ✅ naprawiony w PR #713 (2026-09-27), za zgodą właściciela. Ta sama przyczyna wywracała
  też widok wątku (`/wiadomosc/<pk>/`, także `?fragment=1`) i szufladkę wiadomości — wiadomości
  systemowe mają `sender=None`. Szablony: `{% if x %}…{% else %}system{% endif %}`.

- **Trasa:** `ui:messages_admin` → `GET /wiadomosci/panel/` (Lider kontroli, Administratorzy)
- **Opis:** `notifications.py:82` tworzy wątek systemowy (`SYSTEM_THREAD_SUBJECT`) bez `created_by`;
  `created_by` jest też zerowany (`SET_NULL`) po usunięciu autora. Szablon
  `ui/messaging/admin.html:21` ma `{{ t.created_by.get_full_name|default:t.created_by.username|default:"—" }}`
  — argument filtra `default` rozwiązywany jest bez wyciszania błędów, więc `None.username` wywraca
  cały widok (TypeError → 500). Jeden taki wątek = panel nieczynny dla wszystkich liderów.
- **Kroki:** dowolne powiadomienie systemowe tworzy wątek → lider otwiera „Panel nadzoru” → 500.
- **Oczekiwane:** 200, autor „—” / „system”.
- **Wykrył:** crawler audytu UX (etap 1, 2026-09-27) + `ui.tests.test_messages_panel_b012` (`expectedFailure`).
- **Test regresji:** `ui.tests.test_messages_panel_b012.MessagingWithoutAuthorTests` (panel, wątek, fragment, szufladka).

## B-013 🟡 Dziennik wydruku HU — 500 przy wydruku usuniętego użytkownika

- **Status:** ✅ naprawiony w PR #714 (2026-09-27), za zgodą właściciela — `{% if r.username %}…{% elif r.user %}…`.

- **Trasa:** `ui:hu_print_log` → `GET /hu-print/log/` (Magazyn, Administratorzy)
- **Opis:** `HUPrintRun.user` to `SET_NULL` (model zachowuje `username` jako migawkę do audytu), ale
  `huctl/templates/ui/hu_print/log.html:58` ma `{{ r.username|default:r.user.username|default:"—" }}` —
  ten sam wzorzec co B-012: argument filtra `default` rozwiązywany jest zawsze, więc `None.username`
  wywraca cały dziennik, gdy choć jeden wydruk należał do usuniętego konta.
- **Kroki:** wydrukuj etykiety → usuń konto drukującego → otwórz dziennik wydruku → 500.
- **Oczekiwane:** 200, autor z migawki `username` (albo „—”).
- **Wykrył:** przegląd wzorca przy naprawie B-012 (2026-09-27) +
  `ui.tests.test_messages_panel_b012.PrintLogWithoutUserTests` (test regresji).

## B-014 🟠 Kolory `var(--…)` w canvas / ECharts / Plotly — biblioteki ich nie rozumieją

- **Status:** ✅ naprawiony w PR #722 (2026-09-27), za zgodą właściciela — w każdym skrypcie
  `cssVar(n)` = `getComputedStyle(...).getPropertyValue(n)`: kolor z tokenu w chwili rysowania
  (idzie za motywem). Sprawdzone w przeglądarce: heatmapa `interpolateColor(0)` → `#f1f5f9` / `#1b2740`,
  opcje ECharts bez `var(`, zero ostrzeżeń kolorów w konsoli.

- **Trasy:** `/magazyn/heatmapa/<pk>/` (heatmapa ruchów), `/planner/analytics/` (wykresy ECharts),
  edycja kartonu (podgląd Plotly), własny układ palety (`/pallets/<pk>/custom/`, Plotly 3D).
- **Opis:** zamiana hex → token (commit `1192ae2`, 2026-07-30, „hex sweep in 75 templates”) wstawiła
  literały `'var(--…)'` także do JS, który przekazuje kolor bibliotece rysującej samodzielnie:
  - `wh3d/.../heatmap/detail.html` — `interpolateColor()` zwraca `"var(--gray-100)"` dla komórek z 0
    ruchów, a `ctx.fillStyle` odrzuca nieznany kolor i **zostawia poprzedni** → puste lokalizacje
    rysują się kolorem ostatnio malowanej (zwykle „gorącej”) komórki; też napis „Brak danych”
    (`fillStyle = 'var(--gray-400)'`) — przekłamanie danych na mapie ciepła;
  - `ui/.../planner/analytics.html` — `itemStyle/lineStyle/axisLabel color: 'var(--…)'` w ECharts
    (renderer canvas) → słupki, linia progu 75 % i etykiety bez zamierzonych kolorów;
  - `carton_form.html`, `pallet_custom_editor.html` — `paper_bgcolor/bgcolor: 'var(--gray-50)'`
    w Plotly → tło wykresu nie idzie za motywem (domyślne białe w ciemnym motywie).
  W DOM (`el.style.x = 'var(--x)'`, atrybuty SVG) `var()` działa — te miejsca są w porządku.
- **Oczekiwane:** literały hex/rgb albo odczyt tokenu w JS
  (`getComputedStyle(document.documentElement).getPropertyValue('--gray-100')`).
- **Wykrył:** etap 8 audytu UX (2026-09-27) przy kolorach sceny 3D — tam ten sam błąd (tabliczka
  co 9. alei w 3D mapy magazynu: `'var(--green)'` → biały słupek) naprawiony w PR etapu 8.
  Test: `ui.tests.test_canvas_css_vars.CanvasCssVarsTests.test_canvas_and_chart_colours_are_literals`
  (`expectedFailure`).

## B-015 🟡 Zadania — link bez „źródła” to sama strzałka „→”

- **Status:** ✅ naprawiony w PR #728 (2026-09-28), za zgodą właściciela — `{{ t.source_ref|default:"Otwórz" }} →`.

- **Trasa:** `ui:tasks_home` → `GET /tasks/`
- **Opis:** `ui/templates/ui/tasks.html:96` renderuje `<a class="text-sm" href="{{ t.url }}"> {{ t.source_ref }} →</a>`.
  `Task.source_ref` jest `blank=True`, więc zadanie z `url`, ale bez źródła, dostaje link, którego całą
  treścią jest „→”: czytnik ekranu czyta „strzałka w prawo” (WCAG 2.4.4 / 4.1.2 — brak sensownej nazwy),
  a cel ma ok. 10 px szerokości. Na danych seed: 4 takie linki na `/tasks/`.
- **Kroki:** zadanie z `url` i pustym `source_ref` → otwórz `/tasks/` → link „→”.
- **Oczekiwane:** czytelna nazwa, np. `{{ t.source_ref|default:"Otwórz" }} →` (jedna linia w szablonie).
- **Wykrył:** pełny skan celów dotyku po audycie UX (2026-09-27) +
  `ui.tests.test_tasks_link_name_b015` (test regresji).
