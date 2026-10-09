# GROOVE / PALVIZ — Audyt UX/UI · Etap 1: Inwentaryzacja długu

> Zakres: **monolit PALVIZ** (aplikacja Django `web/ui`) — wszystkie moduły paska GROOVE.
> Apki-siostry (WHO-SCAN, TIMPORYE, Presebu, compare, HU-CHECK, SPOT) świadomie POZA zakresem.
> Dane: 158 szablonów `.html`, skan warstwy szablonów/CSS/JS/statyki (2026-08-08).

## Korekta przesłanek promptu
- To **Django**, nie Flask; jeden monolit, nie „kilkanaście osobnych repo". Moduły paska =
  funkcje w jednym repo, współdzielą `ui/base.html`.
- Dlatego wspólny pakiet designu (`groove-ui`, Etap 2.4) wewnątrz monolitu to **refactor
  `base.html` + wydzielenie `app.css`**, nie problem dystrybucji między repo. (Problem
  dystrybucji wróci dopiero, gdy audyt obejmie apki-siostry.)

---

## Kluczowe ustalenie: TRZY rozłączne design-systemy

| Layout | Plik | Dzieci | Paleta | Słownik klas |
|---|---|---|---|---|
| Desktop (GROOVE) | `ui/base.html` (699 l.) | ~110 szablonów | niebieski | `.btn/.btn-primary`, `.table`, `.badge`, `.alert`, `.empty-state` |
| Skaner (PWA hali) | `ui/scanner/base.html` (385 l.) | 10 (`scanner/*` ×8, `phv/*` ×2) | **teal** | `.btn--primary/--ok/--err`, `.msg--*`, `.pill--*`, `.card` |
| Auth / druk / e-mail | `auth/auth_card.html` + **20 standalone `<!DOCTYPE>`** | 4 + 20 | własne | własne |

Ten sam przycisk/komunikat/karta istnieje **pod różnymi nazwami klas i w różnej palecie**
w każdej warstwie. To rdzeń długu: nie brzydota, lecz niespójność.

---

## 1.1 Tabela modułów

Framework: Django (wszystkie). Silnik: Django Templates. CSS: **inline** (0 plików `.css`).
Build-step: brak (WhiteNoise serwuje wprost). Język: PL (i18n tylko w ZARIA — PL/EN).

| Moduł | Pliki | Layout | Urządzenie | inline `style=` | własny `<style>` | Nawigacja |
|---|---|---|---|---|---|---|
| Data Center | ~6 | base | desktop | ~85 | 4 | breadcrumb |
| Paletyzacja | ~10 | base | desktop | ~230 | 6 | breadcrumb (część) |
| Opakowania (master) | ~17 | base | desktop | ~230 | 5 | breadcrumb |
| Wycena przesyłek | ~19 | base | desktop | ~330 (`shipment_detail`=182) | 4 | breadcrumb |
| Klienci | 3 | base | desktop | ~31 | 0 | częściowo |
| Magazyn 3D + heatmapa | ~24 | base | oba | ~380 (`warehouse_map/index`=71) | 10 | breadcrumb (część) |
| Kontrola HU | ~15 | **scanner** + base | tablet/skaner | ~430 (`scanner/hu_detail`=95) | scanner | brak breadcrumb |
| Wydruk HU | 3 | base | desktop | 23 | 0 | **brak breadcrumb** |
| Zadania | 2 | base | desktop | 28 | 1 | breadcrumb |
| ZARIA | ~14 | base | desktop | ~200 | 8 | własny (sidebar) |
| Wysyłka UKRAINA | 2 | base | desktop | 36 | 1 | breadcrumb |
| Hierarchia (PHV) | 3 | **scanner** + base | oba | 74 | 1 | brak breadcrumb |
| Admin / core / auth | ~18 | base / auth / own | desktop | ~180 | 4 | breadcrumb (część) |
| Dokumenty / e-mail (druk) | ~11 | **standalone, bez base** | druk | ~120 | wszystkie własne | brak |

**Dług sumaryczny:** **2444 wystąpień inline `style=` w 142 plikach**; **59 szablonów z własnym
`<style>`** (największe: `planner/carton_form`=1219 l., `shipment_detail`=1037 l.,
`warehouse_map/editor`=1135 l., `editor3d`=1004 l., `pallet_custom_editor`=976 l.).

---

## 1.2 Duplikaty komponentów

| Komponent | Ile implementacji | Różnice |
|---|---|---|
| **Przyciski** | 2 rozłączne systemy, 489 użyć/109 plików | Desktop `.btn-primary/-secondary/-danger/-ghost/-sm` (8 wariantów) vs skaner `.btn--primary/--ok/--err/--xl` (BEM `--`, teal); + dziesiątki `<button style="...">` ad-hoc |
| **Tabela danych** | baza `.table` spójna, ale funkcje rozjechane | **Sortowanie: 0** (nigdzie). **Sticky header: 0.** Paginacja: tylko **14/47** plików z tabelami. Eksport: 77 użyć/28 plików, **bez wzorca** |
| **Formularze** | niespójne | `.form-grid` istnieje, ale masa pól inline; walidacja mieszana (`errorlist`/`error-list`/`.errors`), brak jednego wzorca inline-error |
| **Modal / dialog** | **0 komponentu** → 5 ręcznych overlayów | `position:fixed;inset:0` budowane inline (login `#qr-ov` z-50, scanner `#qr-overlay` z-60, zaria `.z-overlay` z-55/60/70) |
| **Toast / komunikat** | 2 systemy, **0 toastów** | Desktop `.alert-{success/error/warning/info}` (Django messages) vs skaner `.msg--*` (+ beep/wibracja). Brak auto-dismiss/toast queue |
| **Badge / status** | wspólna klasa, **rozjechane znaczenia** (patrz 1.4-P1) | `in_control` w 3 kolorach; czerwony = 5 różnych rzeczy |
| **Nawigacja / breadcrumb** | pasek spójny (extends base), breadcrumb **NIE** | Breadcrumb w ~80, **brak w 31** rozszerzających base (m.in. `hu_print/*`, `planner/products`, `warehouse_map/*`, `zaria/*`) |
| **Stan pusty** | 2 wzorce + ~40 unikalnych stringów | GROOVE `.empty-state` (~40 plików, zdrowy) vs skaner ad-hoc `<div style=...>Brak...`; „Brak danych/pozycji/zgłoszeń..." bez słownika |
| **Stan ładowania** | prawie brak | `.spinner`/`.htmx-indicator` w base, użyte w ~5 widokach; **skeletonów: 0** |
| **Strony błędu** | niekompletne | tylko `403.html` + `csrf_failure.html`; **brak `404.html`, `500.html`** → domyślne strony Django (poza brandem) |
| **Potwierdzenie usunięcia** | 3 podejścia, mieszane | strona `*_confirm_delete.html` (×8) vs inline `confirm()` (~23 pliki) vs form bez potwierdzenia; **5 encji ma OBA naraz** (lista omija stronę przez `confirm()`) |
| **Ikony** | **4 równoległe źródła** | emoji (rejestr modułów) · PNG dark (`pv_icon`, 98 plików) · PNG light (nieużywane, ~420 KB martwe) · inline SVG (64 pliki). Migracja „Claude Design" niedokończona |

---

## 1.3 Ścieżki użytkownika (przekrojowe)

Liczby klików = szacunek z kodu/nawigacji (nie pomiar w aplikacji).

### A. Magazynier: login → Kontrola HU → zgłoszenie problemu → Wydruk HU
- Login → (is_control_only) auto-redirect na skaner. **Świat teal (scanner/base).**
- Skan HU → `scanner/hu_detail` → kontrola zawartości → zgłoszenie problemu (`quality_issues`).
- **Przeskok kontekstu:** Wydruk HU = layout **GROOVE (niebieski, base.html)**, nie skaner.
  Operator w rękawicach przechodzi z dużych teal-owych kafli do gęstego desktopowego UI.
- Punkty zgubienia: (1) zmiana palety/nawigacji między skanerem a Wydrukiem HU; (2) stan pusty
  w skanerze inny niż w GROOVE; (3) brak breadcrumb w obu → brak „gdzie jestem".
- Ponowne wpisywanie danych: numer HU/projekt bywa wpisywany ponownie w Wydruku HU.

### B. Planista: Wycena przesyłek → Paletyzacja → Magazyn 3D
- Wszystko w GROOVE (base), ale **każdy moduł ma inny układ filtrów i tabel** (sortowanie nie
  działa nigdzie → planista scrolluje 33 niestronicowane listy).
- `shipment_detail` (182 inline-style, 1037 l. CSS) — najcięższy ekran, inny wygląd niż reszta.
- Ponowne wpisywanie: wymiary/parametry palety wpisywane w Paletyzacji, mimo że są w danych
  produktu/przesyłki; brak przenoszenia kontekstu między modułami.
- Punkty zgubienia: brak spójnego „następny krok"; eksport raz jest, raz go nie ma.

### C. Kierownik: Zadania → raport → ZARIA → wysyłka mailem
- Zadania (base) → raport (inny układ) → **ZARIA (własna nawigacja sidebar, nie pasek GROOVE
  w treści)** → „Wyślij mailem" (osobny ekran).
- Najspójniejszy z trzech (świeżo ujednolicony w ZARIA), ale ZARIA ma własny język nawigacji
  (sidebar) różny od reszty GROOVE.

---

## 1.4 Rejestr problemów

Typ: SP=spójność, CZ=czytelność, WY=wydajność, DO=dostępność, CP=copy. Wpływ: W/Ś/N.
Koszt: S(≤0.5d) / M(1–3d) / L(>3d).

| # | Moduł | Ekran/obszar | Problem | Typ | Wpływ | Koszt | Propozycja |
|---|---|---|---|---|---|---|---|
| P1 | wiele | badge statusu | `in_control` w 3 kolorach; czerwony = 5 znaczeń (to_recheck/new/escaped/rola/declined) | SP | **W** | M | Jeden token per status; mapa statusów w słowniku; komponent `.status-badge` |
| P2 | wszystkie | cała warstwa | 2444 inline `style=` / 142 plików; 59 własnych `<style>` | SP | **W** | L | Wydzielić `app.css` z komponentami; usuwać inline moduł po module |
| P3 | wszystkie | design-system | 3 rozłączne systemy (desktop/skaner/druk) | SP | **W** | L | Wspólne tokeny; skaner jako motyw tych samych tokenów, nie osobny słownik |
| P4 | wszystkie | tabele | brak sortowania i sticky-header; paginacja w 14/47 | CZ/WY | **W** | M | Wspólny komponent tabeli (sort + sticky + gęstość + eksport) |
| P5 | globalnie | JS | plotly 4,85 MB + echarts 1,12 MB ładowane bezwarunkowo (~6 MB) | WY | **W** | M | Lazy-load/dynamic import tylko na stronach z wykresami |
| P6 | globalnie | JS | 3 wersje three.js; orphan `three.module.min.js`+addons ~407 KB martwe | WY | Ś | S | Usunąć orphan; skonsolidować do jednej wersji |
| P7 | wiele | usuwanie | 3 wzorce potwierdzania; 5 encji ma stronę confirm ORAZ inline `confirm()` | SP/DO | Ś | M | Jeden wzorzec (modal-confirm z nazwą obiektu); wyciąć inline `confirm()` |
| P8 | globalnie | błędy | brak `404.html`/`500.html` → domyślne strony Django | SP/CP | Ś | S | Dodać markowe 404/500 na base.html |
| P9 | wiele | stany | 2 wzorce empty-state; ~40 stringów „Brak..." bez słownika; skeletonów 0 | SP/CP | Ś | M | Jeden `.empty-state` (też w skanerze) + słownik; skeleton dla ciężkich list |
| P10 | ikony | globalnie | 4 równoległe źródła (emoji/PNG dark/PNG light/SVG); ~420 KB martwe (light) | SP | Ś | M | Dokończyć migrację do jednego zestawu; usunąć nieużywane |
| P11 | Kontrola HU ↔ Wydruk HU | przepływ hali | przeskok teal-skaner → niebieski-GROOVE w jednym zadaniu operatora | SP/CZ | Ś | M | Ujednolicić motyw skanera z tokenami; albo Wydruk HU w wariancie skanerowym |
| P12 | wiele | breadcrumb/tytuł | breadcrumb brak w 31 szablonach base (hu_print, products, warehouse_map, zaria) | SP | N | S | Dodać breadcrumb lub świadomie zrezygnować (spójnie per moduł) |
| P13 | formularze | wiele | walidacja niespójna (errorlist/error-list/.errors); pola inline zamiast `.form-group` | SP/DO | Ś | M | Jeden wzorzec pola + inline-error pod polem |
| P14 | standalone | batch/pallet/saved_list | widoki wewnętrzne bez nav/breadcrumb (standalone `<!DOCTYPE>`) | SP | N | S | Wpiąć w base.html albo świadomie zostawić jako druk |
| P15 | i18n | całość poza ZARIA | tylko ZARIA ma PL/EN; reszta zaszyta PL | SP/CP | Ś (W dla UKRAINA) | L | Rozszerzyć i18n na moduły partnerów zagranicznych (start: UKRAINA) |

**Top 5 (największa poprawa / rozsądny koszt):** P1 (statusy), P4 (tabele), P5 (lazy-load ~6 MB),
P2/P3 (tokeny + app.css jako fundament), P7 (usuwanie).

---

## Następny krok
Zgodnie z promptem — **STOP na akceptację.** Po zatwierdzeniu: Etap 2 (wspólny system designu:
tokeny + `app.css` + biblioteka komponentów + strona `/ui/`), potem nawigacja, wzorce, moduły
od najczęściej używanego.
