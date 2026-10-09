# Edytor układu magazynu — część 1: szablony gniazd, adresy, zgodność z EWM

**Status:** projekt zatwierdzony w rozmowie 2026-09-25 (podejście A). Część 1 z 4.
Rewizja 2026-09-25 (po analizie eksportu EWM, patrz „Ustalenia z danych”): szablon **per gniazdo**
(domyślny rzędu + wyjątek gniazda), numeracja gniazd z przerwami, akcja wyjątku `add`.
**Moduł:** `wh3d` — „Modele magazynu” (model w metrach, `WarehouseModel` / `WarehouseModelRack`).

## Cel całości (kontekst)

Hala Logistyczna jest nietypowa (numeracja gniazd od 10/29/70, połówki `-1/-2`, rząd 54 bez
gniazd 31–32). Użytkownik chce budować układ „jak w grze”: przeciągnąć sekcję i dostać cały rząd,
ciąć, przesuwać, zmieniać przejścia, definiować typy lokalizacji i nadawać adresy — najpierw jako
wierną kopię obecnej hali (adresy = EWM), potem jako warianty przebudowy. Budujemy w GROOVE, nie w
Blenderze (adresy, stany SAP i warianty są w aplikacji). Wygląd schematyczny, nie fotorealistyczny.

| # | Część | Zakres |
|---|---|---|
| **1** | **Szablony gniazd + adresy + zgodność z EWM** | ten dokument |
| 2 | Edytor „jak w grze” | widok z góry: rysuj/tnij/przesuń/obróć, pary, szerokość przejścia, kopiuj N×, przyciąganie, cofnij/ponów; podgląd 3D z istniejącego widoku |
| 3 | Adresy i wyjątki w edytorze | adresy na planie, klik w miejsce → typ / połówka / blokada / własny adres |
| 4 | Porównanie z EWM + warianty | braki/nadmiary na planie, wariant = kopia modelu, różnica miejsc i dróg (istniejące `design_kpi`) |

Kolejność 1 → 2 → 3 → 4; każda część to osobne PR-y. Część 1 nie ma edytora — daje dane, na
których edytor pracuje.

## Pojęcia

- **Typ lokalizacji EWM** — istniejący `WarehouseRackType` (kod `0010`/`0050`/`0052`/…): wymiary
  **pojedynczego miejsca**. Bez zmian.
- **Szablon gniazda** (nowy) — opis **słupa regału**: szerokość belki (np. 2700 mm), liczba palet
  na belce (pozycje 0..k-1), głębokość, lista poziomów od dołu: litera, wysokość [mm], typ EWM,
  „dzielony na połówki” (`-1/-2`), udźwig [kg].
- **Rząd** — istniejący `WarehouseModelRack` (pojedynczy rząd = przejście EWM). Dostaje **szablon
  domyślny** i **regułę adresu**: strefa (`zone`), przejście (`rack_id`), **numeracja gniazd**
  (zakresy, np. `10-47,50` — kolejne fizyczne gniazda dostają kolejne numery z listy; pusta =
  `1..n_bays`), **kierunek** (numeracja od początku albo od końca rzędu).
- **Wyjątek** (nowy) — wpis, który nadpisuje wynik szablonu:
  - **wyjątek gniazda** (bez litery): *inny szablon* (np. gniazdo 29 = przejazd 3600 tylko Y/Z),
    *pomiń gniazdo* (fizyczne gniazdo bez adresów);
  - **wyjątek miejsca**: pomiń, **dodaj** (miejsce spoza szablonu), własny adres, inny typ EWM,
    podziel / scal połówki, blokada.

Przykład (przejście 07: szablon domyślny „3 pal. · A X Y Z · 0052/0010”, numeracja `10-48`;
gniazda 30–48 mają wyjątek gniazda „3 pal. · B C½ D½ X Y Z · 0052/0010”):
`B0-07-100A`, `B0-07-100X` … `B0-07-102Z`, … gniazdo 30 → `B0-07-300B`, `B0-07-300C-1`,
`B0-07-300C-2`, `B0-07-300D-1`, `B0-07-300D-2`, `B0-07-300X` … `B0-07-302Z`, dalej `B0-07-310B` …

Format kodu: `{zone}-{przejście:02}-{gniazdo:02}{pozycja}{litera}` + `-{1|2}` dla połówek
(konwencja EWM potwierdzona na eksporcie 37 205 lokalizacji).

## Model danych (migracje w `wh3d`)

- `BayTemplate`: `name`, `beam_mm`, `pallets_per_beam`, `depth_mm`,
  `levels` (JSON: `[{"letter": "B", "height_mm": 1000, "ewm_type": "0050", "split": false, "max_kg": 1000}, …]`),
  `notes`. Walidacja: litery unikalne w szablonie, wysokości > 0, `pallets_per_beam` ≥ 1.
- `WarehouseModelRack` +3 pola (istniejące modele działają bez zmian):
  `template` (FK `BayTemplate`, `SET_NULL`, nullable), `bay_numbers` (str, zakresy `10-47,50`,
  puste = `1..n_bays`), `reverse` (bool, domyślnie False).
- `LocationOverride`: FK `WarehouseModelRack`, `bay` (int, numer gniazda z adresu), `position` (int),
  `letter` (str; **pusta = wyjątek całego gniazda**), `half` (0/1/2), `action` (`template` / `skip` /
  `add` / `rename` / `ewm_type` / `split` / `unsplit` / `block`), `value` (str: nowy kod albo typ EWM),
  `template` (FK `BayTemplate`, `PROTECT`, tylko dla akcji `template`).
  Unikalność: (rząd, bay, position, letter, half, action).

## Generator (czysta funkcja, bez ORM)

`expand_row(row, template, overrides) → list[dict]` w nowym module `wh3d/addressing.py`:
`code, bay, position, level_index, letter, half, ewm_type, blocked, along_m, z_m`.
- `along_m` = położenie środka miejsca wzdłuż rzędu (z `bay_width_cm` rzędu; połówki dzielą
  pozycję wszerz — ta sama reguła co w `blender_stock.SlotLocator`, PR #677);
  `z_m` = suma wysokości niższych poziomów.
- Numery gniazd z `bay_numbers` w kolejności fizycznej (tylko pierwsze `n_bays`; gniazda bez
  numeru nie mają adresów); `reverse` odwraca kierunek wzdłuż rzędu.
- Szablon gniazda = wyjątek `template` albo szablon domyślny rzędu; `skip` gniazda = brak miejsc.
- Liczba gniazd = `n_bays` rzędu.
- `expand_model(model)` = wszystkie rzędy + wykrycie **duplikatów kodów** między rzędami.

## „Wykryj z EWM” (propozycja, nie zapis)

Źródło: aktywny `WarehouseLocationMasterBatch` (na produkcji import EWM ~37 tys.). Dla każdego
rzędu modelu z `zone`/`rack_id`:
1. zbierz kody `zone-rack_id-…`, rozłóż na (gniazdo, pozycja, litera, połówka);
2. **wzór gniazda** = zestaw (pozycja, litera, połówka) + typ EWM litery (większość). Wzór
   **regularny** (pełna siatka pozycji `0..k-1` × litery, połówki per litera) = szablon: dopasuj do
   istniejącego (ta sama liczba palet, litery, połówki, typy) albo zaproponuj nowy (nazwa z liczby
   palet, liter i typów, np. „3 pal. · B C½ D½ X Y Z · 0052/0010”; wysokości z mastera `height_mm`,
   a gdy ich brak — 1000 mm do poprawy w formularzu);
3. gniazdo nieregularne → najbliższy szablon (najmniej różnic) + wyjątki miejsca `skip` / `add`;
   pojedyncze miejsca innego typu → `ewm_type`;
4. szablon domyślny rzędu = najczęstszy w rzędzie, pozostałe gniazda → wyjątek gniazda `template`;
5. numeracja: gdy `n_bays` ≥ rozpiętość numerów EWM → ciągła od najmniejszego numeru, a brakujące
   numery → `skip` gniazda (fizyczne gniazdo bez adresów); w przeciwnym razie numeracja z przerwami
   z EWM (np. `10-47,50`). `n_bays` bez zmian (z rysunku), `reverse` bez zmian.
Zapis zastępuje wyjątki wykrytych rzędów; ponowne „Wykryj” używa istniejących szablonów (bez duplikatów).
Ekran podglądu pokazuje propozycję; dopiero „Zapisz” tworzy szablony, ustawia rzędy i wyjątki.

## Raport zgodności z EWM

Per przejście: **zgodne** / **na planie, brak w EWM** / **w EWM, brak na planie** / **duplikat** /
**rząd bez szablonu** / **brak rzędu na planie** (przejście jest tylko w EWM); rozwijana lista kodów, pobranie XLSX. Sprawdzian części 1: po „Wykryj z EWM”
przejścia 01–54 hali Logistyczna mają 100% zgodności, a każda rozbieżność jest nazwana
(np. przejście 54: gniazda 31–32 jako `skip` albo numeracja `29-30,33-64` — zależnie od `n_bays`;
przejścia 55–82 jako „brak rzędu na planie”).

## Ekrany (Polish-first, design system GROOVE)

- **Szablony gniazd** (Magazyn 3D → Modele → Szablony): lista + formularz z tabelą poziomów
  (typ EWM: podpowiedzi z listy `WarehouseRackType`, ale wolny tekst — typy z „Wykryj z EWM”
  pochodzą wprost z mastera i mogą wyprzedzać słownik typów, więc nie blokujemy zapisu).
- **Widok modelu**: przycisk „Wykryj z EWM” → podgląd propozycji (szablony do utworzenia, rzędy:
  szablon domyślny, numeracja, liczba wyjątków) → „Zapisz” (przelicza i zapisuje atomowo).
- **Zgodność z EWM** (z widoku modelu).
- **Edycja współrzędnych**: +3 kolumny na rząd — szablon domyślny, numeracja gniazd, kierunek.

Uprawnienia: edycja `@_md_role` (jak dziś przy modelach), podgląd i raport `@_planner`.

## Błędy

Rząd bez szablonu → brak miejsc, raport „brak szablonu”. Ten sam kod z dwóch rzędów → „duplikat”
(czerwony). Brak aktywnego mastera → „Wykryj z EWM” nieaktywny z wyjaśnieniem. Nieprawidłowy
szablon (puste poziomy, powtórzona litera) → błąd formularza, nic nie zapisujemy.

## Testy

- `expand_row`: przejście 07 z połówkami, `reverse`, `skip` gniazd 31–32, wyjątek gniazda `template`,
  `add`, `rename`, `split/unsplit`, numeracja z przerwami.
- Wykrywanie: próbka prawdziwych kodów z eksportu EWM (przejścia 07, 08, 34, 38, 48, 54) → oczekiwane
  szablony, numeracja, wyjątki; round-trip „wykryj → rozwiń” = te same kody.
- Raport: zgodne / tylko plan / tylko EWM / duplikat.
- Smoke widoków + role (`test_role_enforcement` wzorzec).

## Ustalenia z danych (eksport EWM, 33 301 kodów B0)

- Przejścia 01–54: 1 848 gniazd, **24 różne wzory gniazd** (np. „A X Y Z” ×951, „B C D X Y Z” ×349,
  „B C½ D½ X Y Z” ×138, przejazd „4 pal. · Y Z” ×72, belka 1825 „2 pal.” ×33). Jeden szablon na rząd
  wymagałby ~7,4 tys. wyjątków miejsc → szablon per gniazdo (domyślny + wyjątek gniazda).
- Połówki `-1/-2` tylko na literach C/D w przejściach 07–14 i 28.
- Numeracja z przerwami: 08/12/16/26/32 (47 → 50), 38 (bez 29), 54 (bez 31–32).
- Typ EWM litery zależy od przejścia (A = 0052 w 07–18, 0050 w 19–38), więc typ jest częścią szablonu.
- Prototyp na pełnym eksporcie: 26 szablonów, 682 wyjątki, 26 300 miejsc, **54/54 przejść zgodnych**,
  0 duplikatów. Eksport nie ma kolumny wysokości → wysokości domyślne (1000 mm) do poprawy.

## Poza zakresem części 1

Edytor graficzny (część 2), klikanie w miejsca na planie (część 3), warianty i drogi (część 4),
przejścia 55–82 (inny rysunek), zmiana parsera mapy (`_parse_loc_code`).
