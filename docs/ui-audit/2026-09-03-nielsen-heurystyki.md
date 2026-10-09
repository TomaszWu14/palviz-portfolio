# UX_AUDIT — GROOVE wg 10 heurystyk Nielsena

Data: 2026-09-03 · Zakres: **cały GROOVE** (191 szablonów: huctl 17, ui 127, wh3d 20,
transport 27) + widoki Django renderujące + JS/CSS. Metoda: 8 równoległych przeglądów
kodu (skaner A/B, panele lidera, rdzeń ui+planner, admin/auth/messaging/ZARIA,
carton_opt/phv/warehouse/misc, wh3d, transport). Tylko analiza statyczna — dynamika
(czasy odpowiedzi, realne dane) oznaczona „do sprawdzenia ręcznie".

Uwaga: audyt wykrył **regresję krytyczną** (brakująca klamra w JS klawiatury
`hu_detail.html` — SyntaxError zabijał cały skrypt ekranu liczenia). Jako świeża
regresja z otwartego PR #612 została naprawiona od ręki (poza zakresem audytu);
`node --check` obu bloków OK.

Legenda heurystyk: H1 status · H2 język · H3 kontrola/undo · H4 spójność ·
H5 zapobieganie błędom · H6 rozpoznawanie>pamięć · H7 elastyczność · H8 minimalizm ·
H9 komunikaty błędów · H10 pomoc.

---

## Tabela zbiorcza (moduł × heurystyka)

| Moduł / grupa widoków | H1 | H2 | H3 | H4 | H5 | H6 | H7 | H8 | H9 | H10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Skaner: base/menu/zone/my_shift/status/find_recipient | ⚠ | ⚠ | ⚠ | ⚠ | ⚠ | OK | OK | OK | ⚠ | ⚠ |
| Skaner: hu_detail/investigation/recheck/history/quality/photo | ⚠ | ⚠ | ⚠ | ⚠ | ⚠ | OK | ⚠ | ⚠ | OK | OK |
| Lider: leader/hub/kpi/tv/gls/err_report | ⚠ | ⚠ | ⚠ | ⚠ | ⚠ | ⚠ | ✗ | ⚠ | ⚠ | ⚠ |
| Rdzeń ui + planner (products/stock/tasks/calc/legacy) | ⚠ | OK | ⚠ | ⚠ | ⚠ | OK | ⚠ | OK | ⚠ | ⚠ |
| Admin/auth/messaging/ZARIA | ⚠ | ⚠ | ⚠ | ⚠ | ✗ | OK | ⚠ | ⚠ | ⚠ | OK |
| carton_opt/phv/warehouse/packspec/slotting/ukraine/DC | ⚠ | OK | ⚠ | ⚠ | ⚠ | ⚠ | OK | OK | ⚠ | OK |
| wh3d (mapa/edytory/model/heatmapa) | ✗ | OK | ⚠ | ⚠ | OK | OK | ⚠ | OK | ✗ | ⚠ |
| Transport (wycena/kierowca/wysyłki/importy) | ⚠ | OK | ⚠ | OK | ⚠ | ⚠ | OK | OK | ⚠ | OK |

(OK = dobrze · ⚠ = do poprawy · ✗ = brak)

**Przekrojowe wzorce problemów** (powtarzają się w wielu modułach):
1. **Brak blokady double-submit + spinnera** na formularzach POST — dziesiątki miejsc
   (najgroźniejsze: „Zaksięguj" w liczeniu, „wyślij wycenę" SMTP, importy XLSX).
2. **Upload `onchange=submit()` bez feedbacku** — wzorzec powtórzony ~19× (data_center,
   stock, products, hub, quality_issues…).
3. **Cichy fail w JS** — `.catch(function(){})` / brak `r.ok` (ZARIA akcje, carton_opt
   metryki, instruction_form wymiary, wh3d fetch sceny, sync offline).
4. **Brak `min=today` na datach** przyszłościowych (transport ×3).
5. **Brak autocomplete/typeahead REF** poza phv (carton_opt, warehouse/search, ukraine,
   leader „zadanie foto", shipment_form select tysięcy produktów).

---

## Sekcje per moduł — znaleziska (plik:linia · problem · propozycja · warstwa)

### 1. Skaner — nawigacja (base, menu, zone_select, my_shift, status, find_recipient)

- `ui/scanner/base.html:472-481` — sync offline: `.catch()` gubi błąd serwera, kolejka wisi bez wyjaśnienia → komunikat „Synchronizacja nieudana — ponowię" [JS]
- `base.html:304-307` — „Wyloguj" bez confirm przy niepustej kolejce offline → confirm gdy `queue().length>0` [JS]
- `base.html:393` — `video.play().catch(e){}` — czarny ekran kamery bez komunikatu [JS]
- `base.html:306` — „Wyloguj" mały cel dotykowy (<44px, rękawice) [szablon]
- `menu.html:59` — „skaner gotowy" statyczny, nic nie sprawdza → usunąć lub powiązać z focusem [szablon]
- `menu.html:72` — submit skanu bez blokady double-submit [JS]
- `menu.html:63` — pole bez label i bez przykładu formatu (find_recipient ma „np. 111835083") [szablon]
- `zone_select.html` — brak „Wstecz" gdy wejście z nagłówka [szablon]
- `my_shift.html:10` — „zmień proces" vs wszędzie „strefa" — mieszanie pojęć [szablon]
- `my_shift.html:60` — ikona skanu „▣" inna niż aparat SVG w menu [szablon]
- `status.html` — brak znacznika świeżości „stan z HH:MM" [szablon/backend]
- `find_recipient.html:16` — dwa formularze GET nadpisują sobie kontekst (skan vs szukanie) bez ostrzeżenia [szablon]
- `hu_queue.py` (find) — wyniki bez paginacji — do sprawdzenia ręcznie na realnych danych [backend]

### 2. Skaner — transakcja (hu_detail, investigation, recheck, history, quality, notifications, photo)

- `hu_detail.html:474` — „Zaksięguj" bez disabled/spinnera (double-submit) [JS]
- `hu_detail.html:306` — „Potwierdź" pozycji: to samo + upload zdjęcia na łączu hali [JS]
- `hu_detail.html:122` — link `?full=1` gubi wpisane, niezatwierdzone ilości bez ostrzeżenia → confirm gdy `.uconv` niepuste [JS]
- `hu_detail.html:474` — disabled tylko dla `reqs_confirmed`, brak dla `short_dated_ack` — niespójna afordancja [szablon]
- `hu_detail.html:10` — „Hist." — skrót/anglicyzm + cel <44px [szablon]
- `base.html:502` vs `_utile.html` — kolejka offline czyta `qty_base/qty_alt`, kafle mają pola `t.field` — **ryzyko: offline zapisze puste ilości; do sprawdzenia ręcznie** [JS]
- `_utile.html:6` — `inputmode="decimal"` a polityka = całe jednostki → `numeric` [szablon]
- `investigation_detail.html:29` — „POTWIERDZAM BŁĄD" (skutek KPI) bez confirm/disabled; „Odrzuć" ma confirm — niespójne [szablon]
- `recheck_list.html:19` — `onchange=submit()` bez wskaźnika [szablon]
- `hu_history.html:23,41` — surowe kody `result`/`from_status→to_status` (ang.) zamiast etykiet PL [szablon]
- `hu_history.html` — brak `{% block back %}`; tabela 7 kolumn na 480px → karty [szablon]
- `quality_issues.html:26` — upload zdjęcia `onchange=submit` bez spinnera; `:30` „Zamknij" bez confirm [szablon]
- `notifications.html:3` — strzałka „←" inline zamiast „‹" z paska; oznaczanie przeczytanych — do sprawdzenia ręcznie [szablon/backend]
- `photo_check.html:18` — „Prześlij i licz" bez disabled podczas uploadu; `:58` — nieodwoływany `createObjectURL` [JS]

### 3. Panele lidera (leader, hub, kpi, tv, gls_report, hu_error_report, scanner_sim)

- `leader.html:53-57,126-152,218-231,296-302` — żadna forma POST bez disabled/spinnera (double-submit = 2× wiadomość/potwierdzenie) → wspólny snippet onsubmit [szablon/JS]
- `leader.html` — brak auto-odświeżania panelu „na żywo" (tv.html ma meta refresh) [szablon]
- `leader.html:220-227` — „zdejmij przydział" bez confirm (Grupa ma) [JS]
- `leader.html:149` — REF z pamięci (`placeholder="np. DMOM10001"`), bez datalisty [szablon+backend]
- `leader.html:299-301` — „🗑 Usuń pozycję" bez undo → rozważyć soft-delete [backend]
- `hu_leader.py:157,241` — nienumeryczny odbiorca → surowy 404 zamiast messages [backend]
- `hub.html:75,330` — import odpala się natychmiast po wyborze pliku, bez confirm/spinnera; dwa identyczne formularze importu na stronie [szablon/JS]
- `hub.html:61-68` — „Odśwież z Power BI" (DAX trwa) bez spinnera [szablon/JS]
- `hub.html:83-87` — surowy `{{ powerbi_error }}` + instrukcja `manage.py powerbi_diag` dla lidera → skrót + `<details>` [szablon]
- `hub.html:277-316` — „Zapisz wybór" typów zmienia zakres kontroli zakładu bez confirm [szablon]
- `kpi.html:53` — nagłówek „wg kontrolera" na sztywno przy `?by=zone`; brak własnego zakresu dat (gls ma od/do) [szablon]
- `gls_report.html:17-18` — brak walidacji od>do (cichy pusty wynik); `:62` — identyfikator `code|default:seq` zamiast `ref`; `:44` — próg `ratio>=2` niewyjaśniony; brak CSV (KPI ma) [backend/szablon]
- `ui/planner/hu_error_report.html:3,5` — podświetla zakładkę „Wysyłki", breadcrumb nie prowadzi do huba; `:13` — parametry CSV bez `|urlencode`; `hu_reports.py` — brak paginacji raportu [szablon/backend]

### 4. Rdzeń ui + planner

- **Wzorzec ×19**: `onchange="this.closest('form').submit()"` uploadów bez spinnera/disabled (`data_center.html:196`, `stock.html:330`, `planner/products.html:362,375`…) → wspólny snippet „Importowanie…" [szablon/JS]
- `products.html:378-389` — purge przez `<select onchange>` z generycznym confirm nie mówiącym CO usunie [szablon]
- `products.html:529` — dwa wzorce potwierdzania delete w jednym module [szablon]
- `instruction_form.html:207` — cichy catch autouzupełniania wymiarów [JS]
- `_messages_bell.html:70`, `_notif_alert.html:111` — cichy fail pollingu (brak sygnału offline) [JS]
- `stock_contents.html:511,503` — akcja masowa „Wywołaj zaznaczone" bez licznika/confirm; `:512-517` — sort ikonami 🔥/⭐ vs strzałki w tej samej tabeli [JS/szablon]
- `tasks.html:639-644` — „Sprawdź niezgodności stocku" bez spinnera [szablon]
- `task_form.html:745-783` — brak dirty-check (utrata opisu przy Anuluj); checklist pełny reload per klik [JS]
- `batch_detail/pallet_detail/saved_list` — legacy: własny CSS, branding „Paletyzacja PRO" zamiast `{{ app_name }}`, twarde URL-e, inny format dat → migracja na ui/base [szablon]
- `pallet_detail.html:1018` — HTMX bez `hx-indicator` [szablon]
- `axes_lockout.html:1269-1273` — ścieżki adminowe pokazywane zwykłemu userowi [szablon]
- `data_center_packaging.html:1377-1382` — wagi jako text bez `inputmode`/step [szablon]
- `report_detail.html:69` — `{{ form.status }}` — czy choices PL? do sprawdzenia ręcznie [backend]

### 5. Admin / auth / messaging / ZARIA

- `admin/module_access.html:219-222` — „Odrzuć zmiany" bez confirm przy dirty; brak `beforeunload`; `:151` „Zapisz macierz" bez disabled; `admin_users.py:233-234` — zapis delete+bulk_create bez ochrony przed równoległym adminem [JS/backend]
- `admin/users.html:115-118` — „Dezaktywuj" bez confirm (odcina konto!); `:59-67` import bez spinnera; `:21` „8 grup" a jest 9 (też `panel.html:37`) [szablon]
- `admin/user_confirm_delete.html:17` — nie mówi, co znika z kontem [szablon]
- `admin/control_zones.html:92` — zmiany checkboxów giną cicho bez zapisu wiersza [JS]
- `admin/user_form.html:98-102` — zgodność haseł dopiero po submit; `:117-124` — checkboxy ról aktywne mimo „stanowisko ustawia role"; breadcrumb „Planer" vs „Panel admina" [JS/szablon]
- `messaging/admin.html:24` — N+1 `t.messages.count`; brak paginacji/filtrów [backend]
- `messaging/compose.html:47`, `thread.html:20-24` — double-submit; brak licznika znaków (maxlength 400); brak Ctrl+Enter; utrata tekstu po 500 [JS/szablon]
- `zaria/conversation.html:270-283,365-369` — akcje ★/Przypnij/rename/choose: fetch bez `.catch()`/`r.ok` — cicho nie robi nic; 2× kopia `csrf()` [JS]
- `zaria/home.html:35` — `health_detail` może wylać surowy błąd techniczny [backend]
- `registration/login.html` — brak double-submit na „Zaloguj się" (drobne) [JS]

### 6. carton_opt / phv / warehouse / packspec / slotting / ukraine / DC

- `carton_opt/inbox.html:79-83,125-130` — zmiana statusu i „Utwórz projekt" bez confirm/double-submit [szablon/JS]
- `carton_opt/redesigns.html:31`, `variants.html:51` — REF bez typeahead (phv ma wzorzec) [JS]
- `carton_opt/redesign_detail.html:159` — `catch(()=>{})` metryk — badge „nie przeliczono" [JS]
- `redesign_detail.html:60-74` vs `variants.html:217-219` — niespójne min/max wymiarów [szablon]
- `phv/home.html:637` — „Wyślij zgłoszenie" bez disable (podwójny tap = duplikat) [JS]
- `phv/reports_admin.html` — read-only bez akcji/linku do zgłoszenia [szablon/backend]
- `warehouse/search.html:271-276` — brak typeahead REF [JS]
- `warehouse/instruction.html:247-249` — ◀/▶ bez disabled na granicach i „x z N"; `:312-313` — martwy fetch przy zmianie warstwy (odpowiedź wyrzucana) [JS]
- `warehouse/error_report.html:28-30` — pyta o imię zalogowanego usera → prefill [backend]
- `packspec/list.html:19-24` — upload nadpisuje partię bez confirm/spinnera [szablon/JS]
- `slotting/analysis.html:82` — pusty Pareto = biały box bez „brak danych" [JS]
- `ukraine/home.html:35-42` — import-upsert bez podsumowania ile zmieni; `line_form.html:31` — indeks bez walidacji vs master data [backend]
- `data_center/matrix.html:133-139` — klikalne `<i>` zamiast `<button>` (brak klawiatury); `:156-159` — upload bez spinnera + `reload()` kasuje filtr; błędy przez `alert()` [szablon/JS]
- `data_center/dictionary.html:39-42` — szukajka bez submitu/hinta „Enter" [szablon]

### 7. wh3d

- `warehouse_map/detail.html:222` — fetch ~37k lokalizacji bez spinnera i bez catch — czarny canvas / cicha śmierć modułu ES [szablon]
- `detail.html:255` — brak guarda WebGL (pusty canvas bez komunikatu) [JS]
- `detail.html` — brak wyszukiwania lokalizacji (edytor ma) i skrótów klawiszowych kamery [szablon/JS]
- `editor.html` — **brak undo (Ctrl+Z)** — rollback tylko przy błędzie serwera [JS]
- `editor3d.html:889-891` — zapis tylko localStorage + `alert('Zapisano!')` — ryzyko utraty pracy między komputerami nienazwane [szablon/JS]
- `warehouse_model/view.html:189` — „Obracaj: PPM" vs mapa obraca LPM — niespójne sterowanie 3D; brak spinnera/guarda WebGL [JS]
- `rack_type_form.html:176-179` — brak walidacji sumy wysokości poziomów vs wysokość regału [backend]

### 8. Transport

- `shipment_quote.html:77-80` — „serwer" (SMTP) bez disabled/confirm → 2 maile do spedycji [szablon]
- `quotes.py:282-283`, `imports_excel.py:236` — surowe `{exc}` w UI [backend]
- `quotes.py:159` — Google Maps synchronicznie bez spinnera; awaria = km znika bez wyjaśnienia [backend/szablon]
- `quote_response.html:122` — „Wyślij wycenę" bez disabled; `:114-115` — daty bez `min=today`; po submit brak ścieżki korekty pomyłki [JS/szablon]
- `driver_form.html:28-30` — rejestracja/telefon bez required/pattern („Dane zapisane" na pustym) [szablon]
- `driver_confirm.html:48-49` — „✕ Nie odbiorę" bez confirm i bez cofnięcia [szablon]
- `shipment_detail.html:340-355` — auto-mailto bez banera wyjaśniającego; `:497` — status SMS fire-and-forget; `:587` — `client_eta` bez min [szablon/backend]
- `shipment_form.html:76-80` — select tysięcy produktów bez szukania; `:134` — help kontrast ~1.8:1 [szablon]
- `wh_response.html:85` — `ready_at` bez min [szablon]

---

## Priorytety (max 10, wpływ/koszt)

| # | Poprawka | Warstwa | Zakres |
|---|---|---|---|
| 1 | **Wspólny snippet „submit z feedbackiem"** (disabled + spinner) i podpięcie do ~30 krytycznych formularzy: Zaksięguj/Potwierdź (hu_detail), wyślij wycenę/SMTP, Zapisz macierz, panel lidera, compose | JS (1 plik) + szablony | cały GROOVE |
| 2 | **Wspólny wzorzec uploadu** („Importowanie…", disabled label, confirm dla nadpisujących) — ~19 miejsc `onchange=submit` | JS+szablon | ui/hub/packspec/DC |
| 3 | Confirm na akcjach o dużym skutku bez potwierdzenia: **Dezaktywuj użytkownika**, „Nie odbiorę" kierowcy, „POTWIERDZAM BŁĄD", zdjęcie przydziału, zapis typów kontroli | szablon | admin/transport/huctl |
| 4 | **Cichy fail JS → komunikat**: ZARIA akcje (★/pin/rename/choose), sync offline skanera, carton_opt metryki, dzwonek/notif offline-badge | JS | 5 plików |
| 5 | **wh3d mapa: spinner + try/catch fetch + guard WebGL** (dziś: czarny ekran bez słowa) | szablon/JS | detail.html, view.html |
| 6 | `hu_detail`: confirm przy `?full=1` z niezapisanymi ilościami + disabled dla short_dated jak dla reqs | JS/szablon | 1 plik |
| 7 | Surowe błędy techniczne w UI: `{exc}` SMTP/import, `powerbi_error`+manage.py, `health_detail` ZARIA, axes_lockout ścieżki adminowe → ludzkie komunikaty + szczegóły w `<details>`/logu | backend/szablon | 5 miejsc |
| 8 | Daty bez `min`: quote_response, client_eta, wh_response (+walidacja od>do w gls_report) | szablon | transport/raporty |
| 9 | Typeahead REF wielokrotnego użytku (wzorzec z phv/home) → carton_opt, warehouse/search, leader foto-task, ukraine | JS | 4 ekrany |
| 10 | Surowe kody w hu_history (`result`, statusy) → etykiety PL; „Hist."→„Historia" z celem ≥44px | szablon | 1 plik |

Świadomie poza top10 (większe): migracja legacy saved/batch/pallet na ui/base, undo w edytorze 2D wh3d, paginacje (find_recipient, err_report, messaging), auto-refresh panelu lidera, wersjonowanie zapisu macierzy uprawnień.

---

## OK — nie zepsuć przy refaktorze

- **Offline-first skanera** (`ui/scanner/base.html`): badge OFFLINE z `aria-live`, kolejka localStorage z replayem `hu_control_sync`, beep+wibracja na błąd, fallbacki dla starego Chrome Zebry, pinch-zoom dozwolony.
- **Skan aparatem**: 2 zgodne odczyty + jawne potwierdzenie ✓, celownik, fallback alert bez BarcodeDetector.
- **hu_detail**: ukrycie oczekiwanej ilości przed zliczeniem (anty-zgadywanie), confirm przy włączaniu flagi błędu, wymuszone zdjęcie dla uszkodzenia/ułożenia, sticky nagłówek+postęp, rekontrola przez innego kontrolera, wymagane powody przejęć/otwarć.
- **Wzorce do kopiowania**: `calc_index` spinner „Obliczanie…"; `carton_form` debounce+catch+live preview; `pallet_custom_editor` confirm z liczbą kartonów + disabled na czas fetch + toast błędu z re-enable; **phv/home typeahead** (klawiatura, textContent/XSS-safe); `bins.html` liczniki w filtrach + noscript; `device_select` „ostatnio na tym urządzeniu"; **module_access save-bar** (dirty counter + disabled Zapisz); **ZARIA SSE** (stop przez AbortController, typing, degradacja bez JS); **editor.html wh3d** save-indicator z rollbackiem; **editor3d** skróty G/R/Del/Esc z guardem na inputach; kalibracja DPI symulatora.
- **Transport**: bramka double-SMS + E.164, fallback SMTP→mailto z prawdziwym powodem, walidacja dat wyceny front+backend, atomowy wybór oferty, jawne „waluty nieporównywalne".
- **Backend**: `django.messages` wszędzie po akcjach; `_kpi_stats` jako jedno źródło prawdy hub/KPI/CSV; `_parse_date_any` chroni przed 500; locki + TOCTOU-rechecki w huctl; empty-states po polsku z następnym krokiem.

## Do sprawdzenia ręcznie

- Zgodność nazw pól kolejki offline (`qty_base/qty_alt`) z kaflami `t.field` — ryzyko pustych ilości w replayu.
- Polskie `get_status_display`/choices w hu_history i report_detail.
- Paginacja/wydajność: find_recipient przy dużych wynikach, hu_error_report po miesiącach, messaging/admin N+1, scena wh3d przy 37k instancji.
- Czytelność skali heatmapy przy skrajnych wartościach; BarcodeDetector na realnym Chrome Zebry; realne czasy importów XLSX i DAX Power BI.
