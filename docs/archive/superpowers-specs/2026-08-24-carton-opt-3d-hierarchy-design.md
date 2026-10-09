# Optymalizacja opakowań 3D — hierarchia sztuka→OPZ→karton→paleta

Data: 2026-08-24
Moduł: `carton_opt` (Optymalizacja kartonów) · ekran „Warianty kartonów"
Branch: `claude/carton-opt-3d-hierarchy`

## Problem

Obecny ekran optymalizacji (`carton_opt/variants.html`) pokazuje **tylko jeden render
palety** (canvas 360px, wizualnie ścięty) i pozwala edytować **wyłącznie wymiary
kartonu**. Planista nie widzi, jak zmiana wymiaru opakowania pośredniego lub najmniejszej
jednostki kaskaduje w górę aż do palety. Celem jest pokazać **całą hierarchię opakowań** i
pozwolić eksperymentować z wymiarami na każdym poziomie, patrząc jak przebudowuje się
paleta.

## Decyzje (potwierdzone z użytkownikiem, 10 pytań)

1. **Układ:** Wariant **A** (obecny widok) stały u góry, nietykalny. Pod nim jedna sekcja z
   przełącznikiem **B ↔ C** (zakładki).
2. **Edytowalne w B/C:** wymiary **sztuki/opakowania + OPZ + kartonu**; paleta liczona
   automatycznie.
3. **Wizualizacja:** **4 osobne rendery 3D pod sobą**, od najmniejszej jednostki:
   `sztuka/opakowanie → OPZ → karton → paleta`.
4. **Render:** wysokość ~**720px** (2×), `width:100%`, kamera w naturalnych proporcjach
   (paleta nie ścięta). Dotyczy wszystkich 4 renderów.
5. **Źródło sztuki/OPZ:** z `build_hierarchy(product)` jako baza, edytowalne w B/C.
6. **Kaskada:** **pełne geometryczne przepakowanie na każdym poziomie** (realne pakowanie z
   rotacją): sztuka→OPZ, OPZ→karton, karton→paleta.
7. **Trwałość B/C:** zapisywane w bazie z historią (simple_history).
8. **Promocja:** „Przenieś do instrukcji" zapisuje **wszystkie poziomy** (sztuka + OPZ +
   karton + nowa wersja instrukcji). ⚠️ fragment wrażliwy — patrz Etap 2.
9. **Info per poziom:** pełna tabelka spec pod każdym z 4 renderów.
10. **Miejsce:** rozbudowa istniejącego `carton_opt/variants.html` (ten sam URL).

## Ustalenie techniczne (kluczowe)

W obecnym kodzie **tylko karton→paleta jest liczone geometrycznie** (`_variant_fill` →
`pallet_calculator`, `layout.placements`). Niższe zagnieżdżenia są **zapisanymi
krotnościami** (`hierarchy.py`):

- `pcs_per_inner_pack` / `ip.units_per_pack` — szt na OPZ,
- `packs_per_carton` — OPZ na karton.

Decyzja #6 wymaga policzenia tych krotności **geometrycznie**. Silnik pakujący jest
**bezwymiarowy** — ten sam kod, który układa kartony na palecie, policzy sztuki w OPZ i OPZ
w kartonie. Reużywamy go, zamiast pisać nowy packer.

## Architektura

### Silnik — `pack_into(container_lwh, unit_lwh) -> {count, fill_pct, per_layer, layers}`
Cienka nakładka na istniejący packer (`palletizer` / `_variant_fill`). Bezwymiarowa,
z rotacją. Wołana na każdej granicy zagnieżdżenia:
- `pack_into(OPZ_dims, sztuka_dims)` → szt/OPZ,
- `pack_into(karton_dims, OPZ_dims)` → OPZ/karton,
- `pack_into(paleta_dims, karton_dims)` → kart/paletę (dziś działa).

Zmiana dowolnego wymiaru w B/C przelicza wszystko w górę. Zerowy/za duży wymiar →
błąd na poziomie, nie crash (twarde ostrzeżenie, jak dziś w `_calc_shipment_data`).

### Widok — `carton_opt_variants` (rozbudowa)
Kontekst dokłada: bazę hierarchii (A) + zapisane warianty B/C + dla każdego 4 poziomy
`{dims, three_data, spec}`. Render przez `palviz-three.js renderPalVizLevel` (reużycie).

### Model — rozszerzenie wariantu
Dziś wariant to `CartonAlternative` (`models.py:1889`, relacja `product.carton_alternatives`)
i trzyma tylko `length_cm`/`width_cm`/`height_cm` kartonu. Dokładamy pola wymiarów
**sztuki** i **OPZ** + `slot` (`B`/`C`). Nowa migracja + rejestracja w simple_history. Bez
ruszania modeli master-danych opakowań (to Etap 2).

### Szablon — `variants.html`
- Sekcja A (istniejąca) na górze bez zmian funkcjonalnych; tylko render palety urośnie do
  720px/pełna szerokość (współdzielony styl).
- Sekcja B/C: zakładki + 4 rendery pod sobą + formularz wymiarów per poziom + mini-spec.

## Podział na etapy

### Etap 1 (ten spec → plan → PR) — wizualizacja + kaskada, BEZ zapisu do master-danych
- `pack_into` + testy silnika.
- Rozszerzenie modelu wariantu (sztuka/OPZ/slot) + migracja.
- Przebudowa `variants.html`: A stały, przełącznik B/C, 4 rendery + mini-spec, render 720px.
- Kaskada przeliczeń na żywo przy edycji wymiarów.
- **Bez** promocji wielopoziomowej — „Przenieś do instrukcji" działa jak dziś (karton).

### Etap 2 (osobny spec → plan → PR) — promocja wszystkich poziomów (decyzja #8)
- „Przenieś do instrukcji" zapisuje sztukę (`Product.unit_*`), OPZ (`InnerPack.*`) i karton
  (nowa `PalletizationInstruction`).
- Transakcja atomowa, walidacja wymiarowości, wpis do historii, potwierdzenie w UI.
- ⚠️ Pisze do master-danych opakowań — najwyższe ryzyko regresji; osobne, dokładne testy.

## Testy (DoD)

- `pack_into`: mniejszy unit → nie mniej sztuk; rotacja działa; zero-wymiar → błąd nie crash.
- Kaskada: zmiana wymiaru OPZ zmienia szt/OPZ i propaguje do kart/paletę.
- Widok: A renderuje jak dziś; B/C pokazują 4 poziomy + mini-spec; render palety 720px.
- (Etap 2) Promocja: transakcja zapisuje 3 poziomy + instrukcję; rollback przy
  niewymiarowości.

## Poza zakresem

- „Opakowanie handlowe" (sales_unit) — hierarchia celowo pokazuje kanoniczne 4 poziomy.
- Zmiana silnika pakującego (`pallet_calculator`) — reużywamy, nie modyfikujemy.
- Wariant A — funkcjonalnie nietykalny (tylko wzrost renderu).
