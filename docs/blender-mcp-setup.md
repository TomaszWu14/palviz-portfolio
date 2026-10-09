# Animacja przepływów magazynu w Blenderze (+ Blender MCP)

Model magazynu (moduł **Magazyn 3D → Modele magazynu**) można wyeksportować do
Blendera i dostać animację przepływów: **wózki widłowe z paletami** (przyjęcie
dok → regał, wydanie regał → dok), **ludzi kompletujących kartony** oraz **linie
przepływów** na posadzce (zielone = przyjęcie, czerwone = wydanie, niebieskie =
kompletacja). Trasy omijają regały (A* po siatce posadzki), wózek podnosi widły na
wysokość poziomu gniazda.

```
GROOVE (Django)                          Blender (lokalnie)
/magazyn/model/<pk>/blender.json  ──▶   tools/blender/palviz_warehouse_anim.py
  wh3d/blender_scene.py (sceny)           buduje halę + klatki kluczowe, render
  wh3d/blender_route.py (trasy A*)        ▲
  wh3d/blender_agents.py (osie czasu)     └─ Blender MCP (opcjonalnie): Claude steruje Blenderem
```

> **Animacja bez Blendera:** ten sam JSON odtwarza się w aplikacji — widok 3D modelu → panel
> **„Animacja przepływów”** (`/magazyn/model/<pk>/przeplywy.json`, te same parametry URL, plus
> `?pickers=1–10` i `?picks=1–25`). Blender jest potrzebny tylko do filmów/renderów.
> Plan dalszych kroków: [`superpowers/specs/2026-09-26-projektowanie-magazynu-design.md`](superpowers/specs/2026-09-26-projektowanie-magazynu-design.md).

## 1. Eksport sceny

W widoku 3D modelu kliknij **Eksport do Blendera** (pobiera
`palviz_model_<pk>_blender.json`). Parametry URL:

| Parametr | Znaczenie |
|---|---|
| `?batch=latest` | kompletacja wg ostatniego importu aktywności pickerów (heatmapa) — realna kolejność pobrań per picker (domyślne w przycisku) |
| `?batch=<id>` | konkretny import `PickerActivityBatch` |
| brak `batch` | kompletacja demo (deterministyczna symulacja) |
| `?forklifts=N` | liczba wózków demo 0–10 (domyślnie 3) |
| `?wt=latest` / `?wt=<id>` | wózki z importu zadań magazynowych EWM (`WarehouseTaskBatch`, Magazyn 3D → Zadania magazynowe EWM) zamiast demo |
| `?wt_from=2026-03-02T06:00` | początek okna (czas lokalny; domyślnie 1. pełna godzina importu) |
| `?wt_hours=1` | długość okna 0,25–24 h (domyślnie 1 h; limit 150 zadań na scenę, nadmiar przycięty i oznaczony w `source.tasks.truncated`) |
| `?wt_scale=1` | kompresja postojów między zadaniami 1–120× (jazda zawsze z realną prędkością) |
| `?snapshot=latest` / `?snapshot=<id>` | zajętość i blokady lokalizacji z eksportu SAP (`WarehouseSnapshot`), plus max wysokość ładunku |
| `?pallets=0` | bez palet w lokalizacjach (same przepływy) |

### Palety w lokalizacjach („cyfrowe zdjęcie" magazynu)

Scena zawiera `pallets[]`: każda zajęta albo zablokowana lokalizacja staje się paletą
w swoim gnieździe regału. Źródła łączone po kodzie lokalizacji:

- **palety HU na stanie** (`Shipment.is_stock`): SKU, nazwa, LOT, najbliższy termin ważności, ilość, numery HU,
- **snapshot SAP** (`?snapshot=`): zajętość, blokada pobrania/odłożenia, max wysokość (→ wysokość bryły),
- **aktywność pickerów** (`?batch=`): liczba pobrań z lokalizacji + klasa ABC SKU (80/95 % pobrań).

Kod lokalizacji parsowany jest jak na mapie 3D: litera = kolumna w boku + poziom
(`A/B/C` = poziom 1, `X/J/K` = 2, `Y/L/M` = 3, `Z/N/O` = 4). Model magazynu zna tylko
**liczbę** boków regału, nie ich kody, więc bok = ranga kodu boku w danych. Lokalizacje,
których regału nie ma w modelu, nie znikają po cichu: ich liczba trafia do `stock_stats.unmapped`.

W Blenderze każda paleta to obiekt z danymi w *Custom Properties* (`kod`, `sku`, `nazwa`,
`lot`, `termin`, `ilosc`, `abc`, `pobrania`, `stan`, `hu`). Zaznacz ją i sprawdź w `N → Item`
albo zapytaj Claude'a przez MCP („co stoi w B0-01-300X?”).

Kolorowanie (`--color-by` albo `ns["recolor"](...)` bez przebudowy sceny), z legendą w kadrze:

| tryb | kolory |
|---|---|
| `state` | zajęta / zablokowana |
| `sku` | stały kolor per SKU |
| `abc` | A (czerwony) / B / C, szary = brak pobrań |
| `expiry` | po terminie / < 30 dni / 30–90 / > 90 dni |
| `picks` | natężenie pobrań z lokalizacji |

```bash
# sam stan magazynu, kolor wg terminu ważności, render jednej klatki
blender -b -P tools/blender/palviz_warehouse_anim.py -- scena.json \
    --no-agents --color-by expiry --render klatki/ --frames 1,1
```

Zadania wózków są na razie **symulacją demo** (brak w GROOVE źródła ruchów
paletowych) — plik mówi to wprost w polu `source`. Lokalizacje spoza modelu
(nieznana strefa/regał) są pomijane.

## 2a. Render bez MCP (headless)

Wymaga Blendera 4.2+ w PATH:

```bash
blender -b -P tools/blender/palviz_warehouse_anim.py -- palviz_model_1_blender.json \
    --blend magazyn.blend --render magazyn.mp4 --engine eevee --speed 4
```

`--speed 4` = 4 s symulacji na 1 s filmu. `--render katalog/` zapisuje klatki PNG,
`--frames 1,240` ogranicza zakres, `--engine cycles` działa też bez GPU.
Bez `--render` dostajesz sam plik `.blend` do otwarcia i dopracowania.

## 2b. Blender MCP — Claude steruje Blenderem

[blender-mcp](https://github.com/ahujasid/blender-mcp) (projekt społecznościowy,
nie oficjalny Blendera) łączy Claude z **uruchomionym** Blenderem przez gniazdo
TCP dodatku. Działa tylko **lokalnie** (Claude Code na komputerze z Blenderem) —
nie w sesji chmurowej.

1. Zainstaluj [`uv`](https://docs.astral.sh/uv/) (dostarcza `uvx`).
2. Pobierz `addon.py` z repozytorium blender-mcp → Blender: *Edit → Preferences →
   Add-ons → Install from Disk* → włącz „Blender MCP".
3. W Blenderze: panel boczny widoku 3D (klawisz `N`) → zakładka **BlenderMCP** →
   **Connect to Claude**.
4. Dodaj serwer do Claude Code (zakres lokalny, tylko u Ciebie):
   ```bash
   claude mcp add blender -- uvx blender-mcp
   ```
   albo skopiuj `tools/blender/mcp.json.example` jako `.mcp.json` w katalogu repo.
   Nie trzymamy `.mcp.json` w repo celowo — sesje chmurowe nie mają Blendera
   i próbowałyby startować martwy serwer.
5. Poproś Claude'a, np.: *„Zbuduj w Blenderze animację z
   `~/Pobrane/palviz_model_1_blender.json` skryptem
   `tools/blender/palviz_warehouse_anim.py`, prędkość 4×, potem pokaż zrzut widoku."*
   Claude wywoła w Blenderze:
   ```python
   import runpy
   ns = runpy.run_path("/ścieżka/PalViz/tools/blender/palviz_warehouse_anim.py", run_name="palviz")
   ns["build"]("/ścieżka/palviz_model_1_blender.json", speed=4)
   ```
   i dalej może przez MCP dopracować scenę (materiały, kamery, oświetlenie, render).

Uwaga: narzędzie `execute_blender_code` wykonuje dowolny Python w Blenderze —
zapisz otwartą pracę przed sesją. Skrypt podmienia tylko własną kolekcję
„PalViz Magazyn" (+ kamerę/słońce „PalViz"), reszty sceny nie rusza.

## Model obecnego magazynu i projektowanie wariantów

**Model obecnego magazynu:** Magazyn 3D → Modele magazynu → **„Model obecnego magazynu (z mapy)”**
buduje regały w metrach z aktywnej mapy lokalizacji i aktywnego mastera
(`wh3d/model_from_layout.py`). Szczegóły i założenia (podziałka gniazda, rozstaw rzędów,
poziomy z mastera) są w notatce modelu. Eksport tego modelu do Blendera jest punktem
wyjścia do wariantów.

**Zestaw projektowy** `tools/blender/palviz_design_kit.py` zawiera parametryczne klocki z katalogu
`web/wh3d/design_catalog.py`. To jedno źródło pojemności, obrysów i wymaganych alejek,
wspólne z GROOVE:

| element | domyślnie | alejka (sprzęt) |
|---|---|---|
| `rack_std` regał paletowy | 10 boków × 3 palety × 5 poziomów | 3,0 m (reach truck) |
| `rack_vna` regał VNA | 20 × 3 × 8 poziomów | 1,8 m (wózek systemowy) |
| `shuttle` regał kanałowy | 6 kanałów × 12 palet × 5 poziomów | 3,0 m na czole |
| `amr` robot AMR/AGV | 1,3 × 0,9 m | 2,0 m |
| `amr_station` stanowisko kompletacji | 2,5 × 2,0 m, 2 porty | — |
| `conveyor` przenośnik rolkowy | 10 m × 0,8 m | — |
| `sorter` sorter z zsypami | 12 m, 10 zsypów | — |

Wartości domyślne to typowe dane katalogowe do porównań, nie oferta dostawcy.

W Blenderze (ręcznie w Scripting albo przez Claude + Blender MCP):

```python
import runpy
kit = runpy.run_path("/ścieżka/PalViz/tools/blender/palviz_design_kit.py", run_name="kit")
kit["start"]("/ścieżka/palviz_model_1_blender.json")      # obecny magazyn jako klocki rack_std
kit["remove"]("B0-")                                        # usuń obecne regały
kit["add_block"]("rack_vna", x=3, y=3, rows=12, bays=33)    # pary plecami + korytarze 1,8 m
kit["add"]("conveyor", x=3, y=36.5, length=90)
kit["add"]("amr_station", x=10, y=38.5)
kit["summary"]()            # miejsca paletowe, zabudowa, ⚠ za wąskie alejki / kolizje
kit["export_variant"]("/ścieżka/wariant_vna.json", name="Wariant VNA")
```

Elementy przesuwasz i obracasz myszką. Po zmianie parametru w *Custom Properties*
(np. `bays`, `levels`) wywołaj `kit["rebuild_all"]()`. Plik `palviz.design-variant` (v1)
zawiera halę, doki, elementy (`kind, label, x, y, angle, params`) i podsumowanie.

### Porównanie wariantów w GROOVE

Magazyn 3D → Modele magazynu → **Warianty projektu** (`/magazyn/warianty/`):

1. **Dodaj wariant bazowy** z modelu obecnego magazynu. Jego regały stają się elementami `rack_std`.
2. **Importuj wariant z Blendera**, czyli plik z `kit["export_variant"](…)`. GROOVE sam przelicza
   wskaźniki z elementów i nie ufa podsumowaniu z pliku. Błędne elementy są pomijane z opisem.
3. Tabela porównawcza (do 6 wariantów, pierwsza kolumna to punkt odniesienia) pokazuje:
   - miejsca paletowe i miejsca na m² hali,
   - powierzchnię zabudowy,
   - drogę do miejsc paletowych: średnią, do strefy A i najdłuższą,
   - naruszenia alejek,
   - liczbę elementów i sprzętu z nominalną wydajnością.
   Zielone tło oznacza najlepszą wartość, a procent to różnicę do pierwszej kolumny.
4. **JSON** przy wariancie pobiera go do dalszej edycji w Blenderze: `kit["load_variant"](ścieżka)`.

Droga to odległość prostokątna od najbliższego punktu obsługi (dok, brama, stanowisko,
stanowisko AMR) do frontu boku regału, ważona liczbą miejsc. Strefa A to 20% miejsc najbliżej
punktów obsługi. Bez doków w modelu droga liczy się od przodu hali, co pokazuje wiersz
„Punkty obsługi”. To miara porównawcza, nie symulacja tras (tę da krok z SimPy).

## Format sceny `palviz.blender-flow` (v2)

Jednostki: metry, sekundy. Układ = plan hali z widoku 3D (x w prawo, y w głąb,
z w górę); skrypt mapuje y → −Y Blendera.

- `racks[]`, `features[]` — jak w widoku 3D (narożnik `x,y`, `angle`, wymiary).
- `agents[]` — `kind` `forklift`/`person`, `keyframes[] {t, x, y, heading, lift}`.
- `items[]` — `kind` `pallet`/`carton`, `appear`/`vanish`, `keyframes[] {t, x, y, z, heading}`.
- `flows[]` — `kind` `inbound`/`outbound`/`picking`/`replenishment`/`transfer`, `points[[x, y], …]`.
- `pallets[]` — `code, x, y, z, heading, w, d, h, level, rack, state, sku, name, lot, qty, unit, hu[], expiry, days_to_expiry, abc, picks`; `stock_stats` — liczniki (w tym `unmapped`).
- `duration`, `source` (`picking`: `picker_activity`|`demo`, `forklifts`: `demo`|`ewm_tasks`, `batch`;
  przy `ewm_tasks` także `tasks_batch`, `window {from, to}`, `time_scale`,
  `tasks {total, loaded, animated, skipped_unmapped, limit, truncated}`).

Cała logika ruchu jest liczona w Pythonie po stronie GROOVE — Blender tylko
interpoluje liniowo klatki kluczowe, więc ten sam JSON da się podpiąć też np. pod
three.js w przyszłości.
