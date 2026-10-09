# Projektowanie magazynu — obecny stan w ruchu + nowy magazyn od zera (warianty)

**Status:** kierunek zatwierdzony w rozmowie 2026-09-26. Część 1 z 7 (animacja przepływów w aplikacji)
dostarczona razem z tym dokumentem.
**Moduł:** `wh3d` — „Modele magazynu” (`WarehouseModel`), „Warianty projektu” (`WarehouseDesignVariant`).
**Powiązane:** [`2026-09-25-edytor-ukladu-czesc-1-design.md`](2026-09-25-edytor-ukladu-czesc-1-design.md)
(edytor układu — jego część 2 to edytor wariantu z kroku 6 poniżej),
[`docs/blender-mcp-setup.md`](../../blender-mcp-setup.md) (format sceny, eksport do Blendera).

## Cel

1. **Obecny magazyn w ruchu** — animacja stanu lokalizacji i przepływów (przyjęcia, wydania,
   kompletacja) z historycznych danych SAP/EWM, w aplikacji (widok 3D modelu), nie w Blenderze.
2. **Nowy magazyn budowany od zera** — 2–3 warianty koncepcji porównane na tych samych danych:
   historia + prognoza wzrostu wolumenów (ML), symulacja, animacja, wskaźniki.

## Decyzje (rozmowa 2026-09-26)

- **Wszystko w GROOVE.** Blender zostaje opcjonalnie — tylko do filmów prezentacyjnych
  (eksport sceny bez zmian). Projektowanie, dane, prognoza, symulacja i animacja są w aplikacji.
- **Obecny magazyn = „wariant 0”.** Ten sam silnik i ten sam format sceny
  (`palviz.blender-flow`) dla obecnej hali i dla wariantów; na wariancie 0 kalibrujemy symulację
  (symulacja vs historia) zanim zaufamy wynikom nowych wariantów.
- **Najpierw liczby, potem rysunek.** Dla magazynu od zera układ hali jest *wynikiem*
  wymiarowania (profil → dzień projektowy → prognoza → wymagania), nie punktem wyjścia.
- **Dane ruchów: zadania magazynowe EWM (WT).** Dostępne do ~3 lat; zakres roboczy **6 miesięcy**
  szczegółowych zadań do profilu i symulacji.
- **Prognoza liczona w GROOVE modelem ML** (nie plan sprzedaży z zewnątrz).
- **Technologie nowego magazynu:** AGV/AMR, **przenośniki przy rozładunku kontenerów**
  (kartony luzem), **wózki kombi** (VNA — składowanie i kompletacja z wysokości); klasyczne
  regały + reach truck jako punkt odniesienia.

## Przebieg (pipeline)

```
zadania EWM + dane materiałowe + stany
      │
      ▼
profil ─► dzień projektowy ─► prognoza ML (scenariusze) ─► wymagania
                                                              │
                         wariant 0 (obecny) ◄── kalibracja    ▼
                                   └──────────► warianty W1..W3 (hala, strefy, sprzęt)
                                                              │
                                                              ▼
                                         symulacja ─► scena palviz.blender-flow ─► animacja
                                                  └─► KPI ─► porównanie wariantów
```

### 1. Dane wejściowe

| Źródło | Pola | Stan |
|---|---|---|
| Aktywność pickerów (`PickerActivity`) | lokalizacja, czas potwierdzenia, picker, materiał, ilość, JM, partia | ✅ jest |
| Snapshot lokalizacji SAP (`WarehouseSnapshot`) | zajętość, blokady, max wysokość | ✅ jest |
| Palety HU na stanie | SKU, partia, termin, ilość, HU | ✅ jest |
| Dane materiałowe | wymiary/wagi, kartony, instrukcje paletyzacji | ✅ jest |
| **Zadania magazynowe EWM (nowy import)** | nr WT, proces/typ zadania (przyjęcie, odłożenie, uzupełnienie, kompletacja, wydanie/załadunek, przesunięcie), lokalizacja źródłowa i docelowa, materiał, partia, ilość + JM, HU źródłowe/docelowe, dokument (dostawa przych./wych.), czas utworzenia i potwierdzenia, użytkownik/zasób, kolejka | ❌ krok 2 |
| Przyjęcia kontenerowe | kontenery/dzień, typ (20'/40'), kartony luzem vs palety, czas rozładunku | ❓ otwarte pytanie |

Import WT: przykładowy eksport z monitora magazynu (`/SCWM/MON`, lista zadań) → CSV/XLSX.
Import musi znieść ~3 lata (partiami, bez ładowania całego pliku do pamięci; indeksy po czasie
potwierdzenia i lokalizacji), choć roboczo używamy 6 miesięcy.

### 2. Profil i dzień projektowy

Per dzień i godzina: palety IN/OUT, linie kompletacji, zlecenia, jednostki; ABC (pobrania),
XYZ (zmienność popytu), maksymalny stan (miejsca paletowe), profil zleceń (linie/zlecenie),
rozkład godzinowy. **Dzień projektowy** = percentyl dnia (domyślnie P95, konfigurowalny)
+ godzina szczytu; na nim wymiarujemy, nie na średniej.

### 3. Prognoza (ML, liczona w GROOVE)

- **Co:** dzienne wolumeny per strumień (palety przyjęte, kontenery, palety wydane, linie
  kompletacji) i per grupa materiałowa / klasa ABC.
- **Dane do prognozy:** 6 miesięcy nie wystarcza do sezonowości rocznej — do prognozy
  wieloletniej używamy **pełnej historii (~3 lata) w postaci dziennych sum** (mały wolumen danych);
  6 miesięcy szczegółowych zadań służy profilowi i symulacji.
- **Model:** do wyboru w specyfikacji kroku 4 (np. gradient boosting na cechach kalendarzowych
  + trend albo model szeregów czasowych ETS/Prophet). **Walidacja wsteczna** (backtest na
  ostatnich tygodniach, WAPE/MAPE) pokazywana w aplikacji obok prognozy.
- **Wynik:** scenariusze P50 (bazowy) i P90 (wysoki) na horyzont N lat + ręczne mnożniki
  (np. nowy klient, nowa kategoria).
- **Runtime:** trening w Celery (`tasks.py`), biblioteki ML importowane leniwie (jak OR-Tools).

### 4. Wymagania (kalkulator)

Per scenariusz prognozy: miejsca paletowe (maks. stan × wzrost ÷ docelowe wypełnienie, np. 85 %),
miejsca kompletacji (SKU A/B w strefie pick), przepustowości (palety/h IN/OUT, linie/h,
kontenery/dzień) → liczba doków i przenośników rozładunkowych, liczba wózków kombi / AGV / osób
(z czasów cykli sprzętu w katalogu).

### 5. Warianty nowego magazynu (od zera)

Przykładowe koncepcje (ostateczne ustalimy na wynikach kroku 4):

| Wariant | Składowanie | Transport / kompletacja | Przyjęcie kontenerów |
|---|---|---|---|
| **W1 Klasyczny** (odniesienie) | regały paletowe | reach truck, kompletacja z poziomu 0/1 | ręcznie na palety |
| **W2 VNA + kombi** | regały VNA, wysoka hala | wózki kombi (składowanie + kompletacja z wysokości) | przenośniki teleskopowe → paletyzacja |
| **W3 Automatyzacja** | regały VNA lub kanałowe (shuttle) | AGV paletowe dok ↔ strefy, AMR / stanowiska goods-to-person | przenośniki teleskopowe → sorter/stanowiska |

Katalog `wh3d/design_catalog.py` ma dziś: `rack_std`, `rack_vna`, `shuttle`, `amr`,
`amr_station`, `conveyor`, `sorter`. **Do dodania:** przenośnik teleskopowy (rozładunek
kontenera), wózek kombi jako sprzęt (wysokość robocza, prędkości jazdy/podnoszenia, czas cyklu,
wymagana szerokość korytarza), AGV paletowy (osobno od AMR). Wartości domyślne = typowe dane
katalogowe; nadpisywalne danymi konkretnego dostawcy.

Hala od zera: wymiary hali i liczba doków wynikają z wymagań; strefy: rozładunek kontenerów /
przyjęcie, bufor, składowanie, kompletacja, pakowanie, wydanie; przepływ w kształcie U albo I.
Wariant rysujemy w GROOVE (edytor wariantu, start od pustej hali — krok 6).

### 6. Symulacja i animacja

- Symulacja zdarzeń dyskretnych (SimPy) dnia projektowego × scenariusz prognozy na każdym
  wariancie; zasoby: wózki/AGV/ludzie/doki/przenośniki; kolejki i czasy cykli.
- Rozmieszczenie towarów (slotting) wg ABC w danym wariancie.
- Wynik: **KPI** (przepustowość vs popyt, wykorzystanie sprzętu, wymagane FTE/wózki/AGV,
  droga, kolejki na dokach) + **scena `palviz.blender-flow`** → ten sam odtwarzacz co dla
  obecnego magazynu (krok 1).
- Kalibracja na wariancie 0: symulacja vs historia (np. linie/h, czasy cyklu) — tolerancja do
  ustalenia na danych.

### 7. Porównanie

Istniejąca tabela „Warianty projektu” (miejsca, zabudowa, drogi, alejki) rozszerzona o KPI
z symulacji i odtwarzanie animacji wariantów; opcjonalnie CAPEX/OPEX wprowadzane ręcznie.

## Kolejność PR-ów

| # | Krok | Zależy od |
|---|---|---|
| **1** | **Animacja przepływów w widoku 3D modelu (obecny magazyn)** — dostarczone | — |
| **2** | **Import zadań magazynowych EWM (6 mies., skalowalny do 3 lat) + animacja z realnych ruchów (wózki i czasy z WT zamiast demo)** — dostarczone | 1 |
| **3** | **Profil + dzień projektowy** — dostarczone | 2 |
| 4 | Prognoza ML (scenariusze P50/P90, backtest) | 2 (+3) |
| 5 | Kalkulator wymagań + katalog: przenośnik teleskopowy, wózek kombi, AGV paletowy | 3, 4 |
| 6 | Edytor wariantu w GROOVE (pusta hala, strefy) = część 2 edytora układu | — |
| 7 | Symulacja wariantów (SimPy) + animacja + porównanie | 5, 6 |

## Część 1 — animacja przepływów (dostarczone)

- **Gdzie:** Magazyn 3D → Modele magazynu → model → widok 3D → panel **„Animacja przepływów”**.
- **Dane:** endpoint `ui:warehouse_model_flow_json` (`/magazyn/model/<pk>/przeplywy.json`) —
  ta sama scena co „Eksport do Blendera” (wspólny `_scene_from_request`), zwracana inline.
- **Źródła:** kompletacja = realna kolejność pobrań z wybranego importu aktywności (albo demo);
  palety na stanie = stan HU (+ opcjonalnie snapshot SAP); wózki = symulacja demo albo zadania
  EWM (część 2). Czasy demo wynikają z tras i prędkości sprzętu, nie ze znaczników SAP.
- **Odtwarzacz:** `wh3d/static/wh3d/js/flow-player.js` (three.js, ładowany dynamicznie — błąd
  modułu nie zabiera widoku 3D): play/pauza, oś czasu, prędkość 1–32×, trasy jako wstęgi na
  posadzce, palety na stanie jako instancje (kolor: stan / ABC / termin / pobrania / SKU),
  klik w paletę → karta lokalizacji.
- **Limity:** pickerzy ≤ 10, pobrań/picker ≤ 25, wózki ≤ 10 — A* ~20 ms na trasę (hala 120×80 m),
  widok jest synchroniczny.

## Część 2 — import zadań EWM + wózki z realnych ruchów (dostarczone)

- **Import:** Magazyn 3D → **Zadania magazynowe EWM** (`ui:ewm_tasks_list`, rola Admin/Master Data):
  CSV/XLSX z `/SCWM/MON` → podgląd (rozpoznane kolumny, mapowanie procesów, próbka, błędne
  wiersze — nic nie trafia do bazy) → import. Plik czytany strumieniowo (`wh3d/ewm_tasks.py`),
  zapis `bulk_create` po 2000 wierszy (`ewm_tasks_import.py`); pliki > 5 MB przez Celery
  (`wh3d.import_warehouse_tasks`, w DEBUG eagerly); gdy brokera nie ma (produkcja bez Redisa/workera
  — stan 2026-09-26) import rusza w wątku w tle procesu web. Raport: wiersze,
  zakres dat, rodzaje ruchu, procesy, błędy, lokalizacje spoza wybranego modelu.
- **Model:** `WarehouseTaskBatch` + `WarehouseTask` (nr WT, proces, rodzaj ruchu, lokalizacje
  i HU źródłowe/docelowe, materiał, partia, ilość + JM, dokument, utworzono/potwierdzono,
  użytkownik, zasób, kolejka); indeksy (partia, potwierdzono) i (partia, lokalizacja).
- **Założenia bez przykładowego pliku (zatwierdzone 2026-09-26):** aliasy nagłówków PL/EN/techniczne
  SAP; wózek = **Zasób**, a gdy pusty — użytkownik potwierdzający; strefa czasowa pliku do wyboru
  (domyślnie Europe/Warsaw); rodzaj ruchu wg rodzaju procesu: `1xxx`/`9010` przyjęcie, `2xxx`
  wydanie (kolejka z „PICK” → kompletacja), `3010–3012`/`301D`/`3100` uzupełnienie, reszta
  przesunięcie; nadpisania `kod = rodzaj` w formularzu importu (zapisywane w partii).
- **Animacja:** `?wt=<id>` + okno (`wt_from`, `wt_hours`, `wt_scale`) — `wh3d/blender_tasks.py`.
  Agent = zasób; zadania wg potwierdzenia, wózek czeka do znacznika zadania (kompresja skraca tylko
  postoje). Przyjęcie = dok → gniazdo, wydanie = gniazdo → dok, kompletacja = gniazdo → stanowisko,
  uzupełnienie/przesunięcie = gniazdo → gniazdo; zadania z wymaganą lokalizacją spoza regałów
  modelu pomijane i liczone. Limit 150 zadań na scenę (A*, widok synchroniczny) — nadmiar
  przycięty z jawnym komunikatem w odtwarzaczu.
- **Uproszczenie:** start ruchu = chwila potwierdzenia (nie potwierdzenie − czas wykonania) —
  do skorygowania przy kalibracji symulacji na wariancie 0 (krok 7).

## Część 3 — profil ruchów i dzień projektowy (dostarczone)

- **Gdzie:** raport importu zadań EWM → **„Dzień projektowy”** (`ui:ewm_tasks_profile`,
  `/magazyn/zadania-ewm/<pk>/profil/?p=90|95|99`).
- **Liczenie:** agregaty w bazie (`wh3d/design_day.py` → `load_inputs`, doba lokalna Europe/Warsaw,
  cache 24 h), czyste liczenie w `build_profile`. Dzień roboczy = ruch ≥ 10 % mediany.
  Strumienie: przyjęcia, wydania, linie kompletacji, uzupełnienia, przesunięcia, zlecenia
  (unikalne dokumenty), razem — średnia, P50, P90/P95/P99, max, godzina szczytu (P wolumenów
  godzinowych z ruchem). Dzień reprezentatywny = realny dzień najbliższy P sumy ruchów (wejście
  do symulacji w kroku 7). ABC wg pobrań (80/95 %), XYZ wg CV dziennych pobrań (0,5 / 1,0),
  profil zleceń (linie/dokument). Pojemność: na razie snapshot zajętości.
- **Ostrzeżenie** przy < 20 dniach roboczych (percentyl ≈ maksimum).
- **Grupy asortymentowe (H1):** pobrania per grupa z danych materiałowych SAP (`MaterialMaster`,
  import „Dane materiałowe SAP” na stronie importów Excel — arkusze Nazwy, Hierarchia produktów,
  Przeliczniki; upsert po MATNR). Łączenie z zadaniami po MATNR (bez zer), zapasowo po REF.
  Przeliczniki (szt. w OPZ/kartonie/palecie, objętości dm³, wagi) — wejście do kroków 4–5.

## Otwarte pytania

1. **Eksport WT z EWM:** przykładowy plik — weryfikacja aliasów nagłówków i mapowania procesów
   (import pokazuje w podglądzie rozpoznane i pominięte kolumny).
2. **Prognoza:** horyzont (lata) i percentyl dnia projektowego (P95?).
3. **Nowa lokalizacja:** wymiary działki, maksymalna wysokość hali, liczba doków, budżet.
4. **Kontenery:** wolumen, typ (20'/40'), kartony luzem vs palety, obecny czas rozładunku.
5. **Wózki kombi:** konkretny typ/producent (parametry katalogowe) czy wartości typowe.
