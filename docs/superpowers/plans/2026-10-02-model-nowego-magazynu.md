# Plan: model 3D nowego magazynu z animacją przepływów (towar, wózki, ludzie)

**Data:** 2026-10-02 · **Status:** etapy 1–7 dowiezione 2026-10-02 (PR #845–#853); E4 w wersji lekkiej
— graficzny edytor „jak w grze” zostaje osobną inicjatywą. Liczby floty zweryfikować na imporcie WT z prod.

**Ścieżka użytkownika:** Generator hali → model 3D z animacją (AGV, kombi, EPT, kontener, owijarka) →
Zadania EWM → Dzień projektowy P95 → Kalibracja na obecnej hali (kt/kp) → Prognoza (mnożnik P50/P90)
→ Symulacja dnia (flota, czekanie, godziny; „Animuj w 3D”) → Porównanie wariantów (kopie modelu
z „Edycji stref” albo z generatora z innymi parametrami).
**Kontynuuje:** [`specs/2026-09-26-projektowanie-magazynu-design.md`](../specs/2026-09-26-projektowanie-magazynu-design.md)
(kroki 1–3 dowiezione: odtwarzacz animacji, import WT z EWM, profil + dzień projektowy).
Ten plan rozpisuje kroki 4–7 speca na małe PR-y `claude/**` i zmienia ich kolejność tak,
żeby **animowany model nowego magazynu był widoczny jak najwcześniej**.

## Co już mamy (nie budujemy drugi raz)

| Klocek | Gdzie | Używamy do |
|---|---|---|
| Odtwarzacz 3D (three.js, play/oś czasu/1–32×, wstęgi tras, palety instancjami) | `wh3d/static/wh3d/js/flow-player.js` | animacja wariantów — ten sam odtwarzacz |
| Agenci z osią czasu: `forklift`, `person`; ładunki `pallet`, `carton` | `wh3d/blender_agents.py` | dokładamy nowe rodzaje sprzętu |
| Trasy A* po siatce posadzki | `wh3d/blender_route.py` (`FloorGrid`) | trasy w wariancie |
| Scena `palviz.blender-flow` (v2) | `wh3d/blender_scene.py` | wspólny format: obecny magazyn = wariant 0 |
| Wariant projektu (`elements` z katalogu, `features` doki/strefy, hala W×D) | `WarehouseDesignVariant`, `wh3d/design_catalog.py`, `design_kpi.py` | geometria nowego magazynu |
| Dzień projektowy P90/95/99, ABC/XYZ, dzień reprezentatywny | `wh3d/design_day.py` | wsad zadań do symulacji |
| Eksport do Blendera (film prezentacyjny) | `tools/blender/palviz_warehouse_anim.py` | opcjonalnie, na końcu |

## Kluczowa decyzja techniczna

**Symulacja = rozszerzony obecny silnik agentów, nie osobny SimPy.** `blender_agents.Agent`
już liczy osie czasu (jazda, obroty, podnoszenie, przenoszenie ładunku). Brakuje tylko
**dyspozytora**: kolejka zadań → pierwszy wolny agent danego typu, plus zasoby z
pojemnością (doki, stanowiska, przenośnik) z kolejką. To kilkadziesiąt linii czystego
Pythona i od razu daje animację + KPI z jednego przebiegu. SimPy dokładamy dopiero, gdy
kalibracja (Etap 6) pokaże, że greedy dispatch nie oddaje rzeczywistości.

## Wzorzec: obiekt klasy EDCO Deurne (decyzja 2026-10-02)

Projekt od zera z parametrów, na wzór centrum dystrybucyjnego EDCO w Deurne (NL).
Dane publiczne/z wyszukiwarki AI — **rząd wielkości, do weryfikacji** (np. 450 × 264 m
= 118 800 m², a podawane jest 107 000 m² użytkowych):

| Parametr | EDCO Deurne | W modelu |
|---|---|---|
| Hala | ~450 × 264 m, wys. 16 m | parametry generatora (E1); u nas 16 vs ~17,5 m (paleta 235 cm) |
| Doki | 60 | **~11–13** z przepływów (patrz niżej), przyjęcie i wydanie po przeciwnych stronach (przepływ I) albo jednej (U) |
| Pojemność | ~210 000 palet | **zadane: 100 000 palet full + 20 000 lok. kartonowych** |
| Strefy pożarowe | 11 kompartmentów | nowy element: ściana/strefa pożarowa (E1) |
| Przyjęcie | kontenery z Dalekiego Wschodu (~80 % asortymentu), kartony luzem | przenośnik teleskopowy + paletyzacja (E2) |
| Transport poziomy | AGV/AMR dok ↔ bufor ↔ korytarze | agent `agv` (E2) |
| Składowanie | VNA, wózki systemowe (Jungheinrich EKX), prowadzenie indukcyjne | `rack_vna` + agent `kombi` (E2) |
| Kompletacja | z poziomów 0/1, operatorzy na wózkach EPT, skanery | agent `ept` + `person` (E2) |
| Pakowanie / wysyłka | przenośniki, sorter, owijarki → doki | `conveyor`, `sorter`, nowa `wrapper` (E2) |

**Sprawdzenie skali (katalog `rack_vna`):** podwójny rząd 2 × 1,1 m + korytarz 1,8 m = 4,0 m;
gniazdo 2,7 m × 3 palety → 0,56 palety/m²/poziom. Przy 16 m w świetle ~9 poziomów (z podłogą)
→ ~5 palet/m² bloku. 210 000 palet ≈ **42 000 m² bloku VNA**, z przejazdami poprzecznymi
~46–50 tys. m², czyli mniej niż połowa hali. Reszta to przyjęcie, bufory, kompletacja,
pakowanie i wysyłka. Liczby się bronią.

### Pojemność zadana (2026-10-02): 100 000 miejsc paletowych full + 20 000 lokalizacji kartonowych (K1)

**Paleta full: 235 cm z nośnikiem** → raster poziomu ~2,60 m (paleta + belka ~0,15 + luz 0,10).
Moduł VNA z katalogu `rack_vna`: 1,1 + 1,8 + 1,1 + 0,2 m = 4,2 m, gniazdo 2,7 m × 3 palety
→ 0,53 miejsca/m²/poziom. Pod tryskaczami ~1 m wolnego.

| Wysokość w świetle | Poziomy (z podłogą) | Góra najwyższej palety | Palet/m² bloku | Blok VNA na 100 tys. + przejazdy ~10 % |
|---|---|---|---|---|
| 16 m (jak Deurne) | 5 | 12,75 m (2,25 m pod dachem zmarnowane) | 2,65 | **~41 500 m²** |
| **~17,5 m** (zalecane do decyzji) | 6 | 15,35 m | 3,18 | **~34 500 m²** |

**Decyzja 2026-10-02: 16 m → 5 poziomów, blok VNA ~41 500 m².** (17,5 m dałoby 6 poziomów
i −7 000 m², odrzucone; generator i tak trzyma wysokość jako parametr, wysokość jako parametr i pokaże oba warianty.

**K1 — osobna strefa półkowa (decyzja 2026-10-02):** 20 000 lokalizacji, regał półkowy
1,0 × 0,6 m, 5 półek × 3 lokalizacje, korytarz 2 m → **~3 000 m²** z przejazdami. Ludzie
oddzieleni od wózków VNA. Z K1 kompletujemy zarówno **palety** (na auta), jak i **paczki**.

### Przepływy zadane (2026-10-02, doba = 2 zmiany 5:00–13:00 i 13:00–21:00, 16 h)

| Strumień | Dziennie | Palet/dzień |
|---|---|---|
| Przyjęcie: kontenery 40', **wyłącznie kartony luzem**, 45 palet z kontenera | 20 | **900** |
| Przyjęcie: auta FTL × 33 palety | 3 | 99 |
| Przyjęcie: dostawy drobnicowe × 1 paleta | 20 | 20 |
| **Razem przyjęcie** | | **~1 020 (~64/h)** |
| Wydanie: auta pełne FTL × 33 palety | 10 | 330 |
| Wydanie: busy × 8 palet | 10 | 80 |
| Wydanie: paczki = 2–3 kartony połączone, **ładowane luzem do 4 małych kontenerów (20')** | 3 000 | ~7 500 kartonów (≈ 150 palet) |
| **Razem wydanie** | | **~410 palet + 7 500 kartonów (~26 palet/h)** |

**⚠ Kontrola bilansu:** wchodzi ~1 020 palet, wychodzi ~560 (410 + ~150 w paczkach) → stan
rośnie ~460 palet/dzień, 100 tys. miejsc pełne w ~7 mies. Wniosek: 20 kontenerów/dzień to
prawdopodobnie **szczyt sezonu**, nie średnia roczna. Do wymiarowania doków przyjęć bierzemy
szczyt (20), do pojemności — średnią z EWM (dzień projektowy, krok 3). Rotacja: 100 tys. miejsc ≈ 3 mies. przyjęć.

Wstępne wymiarowanie (reguły kciuka; doprecyzuje symulacja E3):

| Zasób | Założenie | Wynik |
|---|---|---|
| Doki kontenerowe + przenośnik teleskopowy | rozładunek 45 palet kartonów ~3 h + 0,5 h zmiany; 20 × 3,5 h / 16 h | **5 + 1 rezerwa** |
| Stanowiska paletyzacji (przy każdym przenośniku) | ~15 palet/h; paleta 235 cm → podest/podnośnik dla układającego | **5–6** |
| Ludzie przyjęcie kontenerów | 2 rozładunek + 2 paletyzacja na dok w toku | **~20 / zmianę** |
| Dok przyjęć paletowych (FTL + drobnica) | ~5,5 h/dzień | **1** |
| AGV dok → VNA | ~20 zadań/h, ~64 palety/h | **4 + 1 rezerwa** |
| Wózki VNA (kombi, parametry typowe z katalogu — decyzja 2026-10-02) | ~1 020 odłożeń + ~330 pobrań full + uzupełnienia K1 (~200) ≈ 97 cykli/h, ~20 cykli/h | **5** |
| Kompletujący K1 — paczki | ~7 500 kartonów/dzień, ~100 kartonów/h na osobę (EPT) | **~5 / zmianę** |
| Kompletujący K1 — palety mieszane | z linii kompletacji (dzień projektowy EWM) | do policzenia w E3 |
| Stanowiska łączenia/pakowania paczek | 3 000 / 16 h ≈ 190/h, ~60/h na stanowisko + szczyt | **4** |
| Owijarki palet wydań | ~26 palet/h | **1 + 1 rezerwa** |
| Doki wydań FTL | 10 × ~1 h = 10 h / 16 h | **1 + 1 rezerwa** |
| Brama dla busów 8-paletowych | 10 × ~20 min; niska podłoga → brama z najazdem (poziom 0) albo dok z wydłużoną klapą | **1** |
| Dok załadunku paczek do kontenera 20' + przenośnik teleskopowy | 4 × ~2 h = 8 h; przenośnik działa też „na wyjazd” | **1** (rezerwa = wolny dok kontenerowy przyjęć) |
| Bufor paczek przed kontenerem | paczki z pakowania → przenośnik → kontener (bez sortera) | strefa przy dokach |

Razem **11 bram**: przyjęcia 6 kontenerowych (5 + 1) + 1 paletowa; wydania 2 FTL (1 + 1) + 1 busy + 1 kontener paczek. EDCO ma 60 — tam skala i dystrybucja są kilka razy większe.

**Rząd wielkości hali (16 m):** VNA ~41,5 tys. m² + K1 ~3 tys. + przyjęcie kontenerów
z paletyzacją i buforem ~4 tys. + wydania, pakowanie paczek, załadunek kontenerów i bufory
~4 tys. + drogi AGV → **~54–57 tys. m²**. **Działki brak (2026-10-02)** → generator dobiera
prostokąt ~1,7 : 1, np. **~310 × 185 m**, przepływ I (przyjęcia i wydania po przeciwnych
ścianach — przy 11 bramach i dwóch zmianach rozdziela ruch aut).

**Ważne:** EDCO to wzorzec **technologii i układu**, nie wielkości. Wielkość nowego magazynu
ACME wynika z naszych danych (dzień projektowy z kroku 3 × prognoza z E5): generator
przeskalowuje ten sam układ do wymaganej liczby palet, doków i stanowisk.

## Etapy (każdy = 1–3 PR-y, każdy daje coś widocznego)

### Etap 1 — Generator hali od parametrów → wariant w 3D
*Najkrótsza droga do „widzę nowy magazyn”, zanim powstanie edytor „jak w grze”.*
- Formularz: wymiary hali, wysokość w świetle, liczba doków + strona, kształt przepływu
  (U / I), technologia składowania (`rack_std` / `rack_vna` / `shuttle`), liczba miejsc
  paletowych docelowo, % miejsc pick na poziomie 0, strefy (rozładunek kontenerów, bufor,
  składowanie, kompletacja, pakowanie, wydanie).
- Czysta funkcja `wh3d/design_generator.py`: parametry → `elements` + `features` wariantu
  (rzędy z alejkami wg `aisle_m` katalogu, doki na ścianie, strefy jako prostokąty).
- Widok 3D wariantu w istniejącym widoku modelu (adapter wariant → te same dicty regałów/
  doków/stanowisk, których używa `build_scene`).
- KPI z `design_kpi.py` od razu (miejsca, zabudowa, drogi).
- Preset **„jak EDCO Deurne”** (wartości z tabeli wyżej) + ściany stref pożarowych z bramami.
- Parametry wejścia: miejsca full (100 tys.), lokalizacje kartonowe (20 tys.), wariant K1/K2,
  wysokość palety → generator sam dobiera liczbę i długość rzędów.
- **Skala:** ~120 tys. miejsc to ~5× więcej niż Logistyczna (26 tys.) — widok 3D rysuje
  regały/palety `InstancedMesh` (jak `buildStock`), bez obiektu na gniazdo.
- **Test:** generator — liczba miejsc ≥ zadana, regały nie nachodzą na siebie ani na strefy,
  alejki ≥ `aisle_m`.

### Etap 2 — Katalog sprzętu i ludzi + modele 3D agentów
> **Stan 2026-10-02 — E2a dowiezione:** agenci `kombi` (VNA, kabina jedzie z widłami), `agv`,
> `ept` (picker na wózku paletowym) w `blender_agents.py` + bryły w `flow-player.js`. Scena demo:
> regały ≥ 8 m przy korytarzu ≤ 2,2 m (VNA) → sztafeta AGV (dok ↔ czoło rzędu) + kombi (czoło ↔
> gniazdo); półki (poziom < 1 m) → kompletacja z wózków EPT. Obecna hala (korytarze ~3 m) bez zmian.
> **E2b dowiezione:** kontener 40' przy doku kontenerowym, kartony jadą przenośnikiem
> teleskopowym na paletyzację (pracownik układa paletę), gotową paletę zabiera AGV → kombi;
> wydanie przez owijarkę (60 s). Moduł `blender_containers.py`. Wpisy katalogu (kombi, AGV
> paletowy, przenośnik teleskopowy) — przy kalkulatorze wymagań (krok 5 speca), bo dopiero tam
> są potrzebne poza animacją.

- `design_catalog.py`: **wózek kombi** (VNA, wysokość robocza, prędkość jazdy/podnoszenia,
  czas cyklu, operator jedzie z wideł), **AGV paletowy** (osobno od AMR), **przenośnik
  teleskopowy** (rozładunek kontenera), **wózek EPT** (kompletacja z poziomu 0/1),
  **owijarka**, **reach truck** jako sprzęt odniesienia.
- `blender_agents.py`: `SPEED`/`CARRY_OFFSET` dla `kombi`, `agv`, `reach`; agent stacjonarny
  `person` w roli rozładunek / pakowanie / kompletacja.
- Ruch towaru bez agenta: **karton na przenośniku** (Item z klatkami po linii przenośnika),
  **kontener/naczepa na doku** (Item `container` pojawia się/znika).
- `flow-player.js`: proste bryły (kombi z kabiną na widłach, AGV płaski, ludzie w kolorze
  roli), legenda ról. Wygląd schematyczny (GROOVE), nie fotorealizm.
- **Test:** scena z każdym rodzajem agenta przechodzi walidację formatu; czasy cyklu kombi
  rosną z wysokością gniazda.

### Etap 3 — Dyspozytor + scenariusz dnia na wariancie (animacja „żyje”)
> **Stan 2026-10-02 — E3a dowiezione:** `wh3d/design_sim.py` + ekran „Symulacja dnia na modelu”
> (z dnia projektowego, `ui:ewm_tasks_simulate`). Decyzje usera: dzień **P95**, mnożnik **1,0**
> (pole w formularzu). Droga prostokątna wzdłuż alejek (cały dzień < 1 s), sztafeta AGV → kombi
> z przekazaniem przy czole rzędu bliżej celu, ABC: A = najtańsze 20 % miejsc (blisko i nisko),
> pickerzy EPT objazdami per dokument (≤ 20 linii). Wskaźniki: wykorzystanie, sugerowana flota
> (praca ÷ 16 h × 85 %), czekanie średnio/P95, godziny zlecone vs wykonane, zadania po 21:00.
> Wniosek z próby: AGV robią ~230 m/zadanie przy 1,5 m/s — flota AGV z reguły kciuka (4)
> jest za mała; symulacja sugeruje ~7.
> **E3b dowiezione:** `simulate(trace=…)` zapisuje przebiegi agentów, `design_sim_scene.py` odtwarza
> wybraną godzinę na agentach `blender_agents` (ta sama scena i odtwarzacz). Trasy: korytarz →
> przejazd poprzeczny → korytarz (A* na 300 × 180 m za wolne dla setek ruchów); ≤ 300 przebiegów
> na scenę, przycięcie jawne. Przekazanie palety czeka na faktyczny dojazd (trasy dłuższe niż
> w symulacji). Wejście: „Animuj w 3D →” przy godzinie w tabeli symulacji.

- Wejście: dzień reprezentatywny z kroku 3 × **ręczny mnożnik wzrostu** (np. 1,3×) —
  bez czekania na ML.
- Slotting ABC w wariancie: A najbliżej doków/strefy pick, C wysoko/daleko.
- Mapowanie zadań na proces wariantu: kontener → przenośnik → paletyzacja → AGV/wózek do
  bufora → kombi/reach do gniazda; wydanie: gniazdo → strefa wydań → dok; kompletacja:
  picker / kombi z wysokości / stanowisko AMR.
- `wh3d/design_dispatch.py`: greedy dispatch (zadanie → pierwszy wolny agent typu),
  zasoby z pojemnością (doki, stanowiska, przenośnik) z kolejką.
- Wynik: scena `palviz.blender-flow` + KPI (wykorzystanie sprzętu, czekanie w kolejkach,
  przepustowość vs popyt na godzinę, FTE, km jazdy).
- **Wydajność:** dzień projektowy to tysiące zadań, a dziś limit to 150/scenę, synchronicznie.
  Liczymy w Celery (fallback wątek — jak import WT), scenę zapisujemy w wariancie, odtwarzacz
  ładuje okno czasu (np. godzinę szczytu) zamiast całego dnia. Siatka A* z cache tras
  między parami punktów.
- **Test:** dyspozytor — żaden agent nie wykonuje dwóch zadań naraz, dok nie obsługuje więcej
  pojazdów niż pojemność, suma obsłużonych palet = suma wejściowa.

### Etap 4 — Edytor wariantu (= część 2 edytora układu „jak w grze”)
> **Stan 2026-10-02 — wersja lekka dowiezione (`model_edit.py`):** „Kopiuj jako wariant” (model + regały
> + elementy hali, bez wyjątków adresów EWM) i „Edycja stref” — cała strefa naraz: przesunięcie,
> gniazd w rzędzie, poziomów, usunięcie, wymiary hali (rośnie sama), ostrzeżenie o kolizjach.
> Pojedyncze regały — istniejące „Edytuj współrzędne”, doki/stanowiska — „Elementy hali”.
> **Nie zrobione:** graficzny edytor „jak w grze” (przeciąganie, cofnij/ponów, przyciąganie) —
> osobna inicjatywa z makietami do akceptacji (prompt „Sesja 2” w `.scratch/magazyn-logistyczna/`).

- Ręczne poprawki wygenerowanego wariantu: przesuń/obróć/kopiuj rząd, szerokość alejki,
  doki, strefy, cofnij/ponów. Osobny brainstorming z makietami (prompt „Sesja 2” w
  `.scratch/magazyn-logistyczna/prompty-kolejne-sesje.md`).
- Po zapisie: przelicz KPI + scenę z Etapu 3.

### Etap 5 — Prognoza ML (krok 4 speca)
> **Stan 2026-10-02 — dowiezione (`design_forecast.py`, ekran „Prognoza wzrostu”):** tygodniowe sumy
> (pełne tygodnie, dni robocze) → trend wykładniczy (regresja na logarytmie, stdlib), P50 = trend,
> P90 = trend + 1,28 × błąd nachylenia, mnożnik na horyzont lat, test wsteczny (MAPE ostatnich
> 8 tyg.), per strumień. Przyciski przenoszą mnożnik do symulacji i porównania. Bez sezonowości
> rocznej (wymaga ≥ 2 lat; szczyt niesie dzień P95) — ETS/Holt-Winters, gdy historia na to pozwoli.

- Dzienne sumy z ~3 lat → scenariusze P50/P90 na N lat, backtest WAPE w aplikacji.
- Zastępuje ręczny mnożnik z Etapu 3 (ręczny zostaje jako nadpisanie).

### Etap 6 — Kalibracja na wariancie 0 (obecna hala)
> **Stan 2026-10-02 — dowiezione (`design_calibration.py`, ekran „Kalibracja”):** dla każdego zasobu
> EWM odstęp między kolejnymi potwierdzeniami (≤ 15 min) = realny cykl; ten sam ruch fizyką
> symulacji na modelu obecnej hali → mediana „real ÷ symulacja” osobno dla wózków i kompletacji.
> Symulacja nowej hali przyjmuje te współczynniki (pola „Kalibracja ×”, przycisk z ekranu
> kalibracji). Ostrożne (górne) oszacowanie: krótkie postoje liczą się do cyklu. Wynik zależy
> od importu WT z prod — lokalnie tylko dane testowe.

- Ten sam dyspozytor na modelu Logistyczna + realne WT → porównanie z historią
  (linie/h, czasy cyklu, liczba aktywnych wózków). Korekta parametrów sprzętu i uproszczenia
  „start ruchu = potwierdzenie WT”. Dopiero potem ufamy KPI nowych wariantów.

### Etap 7 — Porównanie wariantów + film
> **Stan 2026-10-02 — dowiezione (`design_compare.py`, ekran „Porównanie wariantów”):** do 4 modeli hali
> na tym samym dniu projektowym (mnożnik + kalibracja): pojemność z regałów, flota dobrana symulacją
> (sugerowana, aż się ustali), droga, czekanie P95, zadania po 21:00; najlepsza wartość wyróżniona.
> Wariant = osobny model (np. z generatora z inną wysokością). CAPEX/OPEX — gdy będą ceny i stawki;
> film z Blendera — istniejący eksport sceny.

- Tabela wariantów W0/W1/W2/W3: miejsca, KPI z symulacji, sprzęt/FTE, odtwarzanie animacji
  obok siebie; opcjonalnie CAPEX/OPEX ręcznie.
- Eksport sceny do Blendera → film prezentacyjny (bez zmian formatu).

## Kolejność i zależności

```
E1 generator+3D ─► E2 sprzęt/ludzie ─► E3 dyspozytor+animacja dnia ─► E7 porównanie
       │                                     ▲         ▲
       └────────► E4 edytor wariantu ────────┘         │
                  E5 prognoza ML ──────────────────────┤
                  E6 kalibracja W0 ────────────────────┘
```
Pierwszy „pokazywalny” efekt (nowa hala w 3D z jeżdżącymi wózkami, AGV, kombi i ludźmi
na realnym dniu × wzrost): **po E1–E3**. E4–E6 można robić równolegle po E3.

## Dane wejściowe — komplet (2026-10-02)

Projekt od zera wg EDCO Deurne, bez działki (~310 × 185 m); hala 16 m; 100 tys. palet full
(235 cm) + 20 tys. lok. K1 (kompletacja na palety i paczki); przyjęcia 20 × 40' (kartony luzem,
45 palet) + 3 × 33 + 20 × 1; wydania 10 FTL × 33 + 10 busów × 8 + 3 000 paczek (2–3 kartony)
w 4 kontenerach 20'; zmiany 5–13, 13–21; sprzęt z katalogu (wartości typowe).

Otwarte tylko do kalibracji (E3/E6): średnia vs szczyt przyjęć (bilans), gabaryty kartonów K1.
