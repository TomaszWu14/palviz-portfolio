# Optymalizacja kartonów — Projekty A/B (redesign opakowań)

Data: 2026-08-20 · Moduł: `carton_opt` (rozszerzenie; Fazy 1–3 wdrożone, Faza 4/3D zrevertowana)

## Cel

Formalny artefakt „projekt przeprojektowania opakowania" dla indeksu: **wersja A =
stan obecny, zamrożony** (wymiary z master daty + wgrany render 3D), **wersja B =
docelowa, projektowana ręcznie** przez człowieka z porównaniem metryk na żywo.
Zakres projektu: OP, karton albo oba. Wynik = **dokumentacja/porównanie** — master
data NIE jest zmieniana automatycznie (wdrożenie fizyczne i zmiana wymiarów w master
dacie to osobny, ręczny krok poza modułem).

Warianty z Fazy 3 zostają jako brudnopis; Projekt A/B to formalny, wersjonowany byt.

## Model danych

`PackagingRedesign` (w `web/ui/models.py`):

| Pole | Typ | Uwagi |
|---|---|---|
| `product` | FK Product | indeks projektu |
| `scope` | Char choices | `op` / `karton` / `oba` |
| `a_snapshot` | JSON | wymiary OP (`unit_l/w/h`), kartonu (`carton_l/w/h`), szt/karton, szt/OP — kopiowane z master daty **przy utworzeniu**; nigdy nie czytane na żywo |
| `a_render` | FileField | `.glb` albo PNG/JPG — render stanu obecnego wgrywany przez użytkownika; podmienialny do akceptacji |
| `b_op_l/w/h`, `b_carton_l/w/h` | Float null | edytowalne; tylko pola objęte zakresem |
| `b_pcs_per_carton`, `b_units_per_pack` | Int null | przeliczniki wersji B |
| `annual_volume_pcs` | Int null | roczny wolumen do oszczędności |
| `status` | Char | `draft` / `accepted` / `rejected`; `accepted` blokuje edycję B i renderu |
| `notes`, `created_by`, `created_at`, `updated_at` | — | standard |

Zasada twarda: **A jest niemutowalne po utworzeniu** — snapshot chroni punkt
odniesienia przed późniejszymi zmianami master daty.

## Ekran i przepływ

Wejścia: (1) przycisk „Utwórz projekt A/B" przy zgłoszeniu w skrzynce carton_opt
(indeks przechodzi automatycznie); (2) „+ Nowy projekt" na liście projektów
(autocomplete indeksu + wybór zakresu).

Ekran projektu — dwie kolumny:
- **A (obecna)**: zamrożone wymiary w tabelce, render 3D (dropzone przy pierwszym
  wejściu), badge „WERSJA OBECNA — niezmienna";
- **B (docelowa)**: pola wymiarów wg zakresu; zmiana pola przelicza metryki na żywo
  (bez zapisu — endpoint JSON `redesign_metrics` liczy silnikiem po stronie serwera,
  bo układ palety liczy `PalletCalculator`, nie JS);
- **pasek porównania** A | B | Δ pod kolumnami;
- akcje: Zapisz szkic / Akceptuj / Odrzuć.

Lista projektów = nowa zakładka „Projekty A/B" w subnav modułu (obok Skrzynki,
Dashboardu, Wariantów): indeks · zakres · status · wypełnienie A→B · data.

Uprawnienia: jak reszta modułu carton_opt (GROUP_OPTIMIZER; Master Data ma wgląd).

## Metryki (A | B | Δ)

Liczone istniejącym silnikiem (jak `_variant_fill`, Faza 3): A ze snapshotu, B z pól.

- wypełnienie palety % (`PalletCalculator`, paleta EU, max wysokość z aktywnej
  instrukcji indeksu),
- szt/karton i szt/paleta (szt/OP × OP/karton × kartony/paletę; zakres „op" →
  karton z A),
- m³ kartonu + zmiana %,
- oszczędność roczna przy `annual_volume_pcs`: palet/rok A vs B → palet mniej +
  szacunek kursów (palet ÷ 33/auto; stała konfigurowalna jak progi pilności Fazy 2).

Δ kolorowane: zielone = B lepsze, czerwone = gorsze.

## Widok 3D obok siebie

Dwa canvasy `renderPalVizLevel` (istniejący renderer, bez zmian JS poza wpięciem):
- A: wgrany `.glb` (renderer wspiera `glb_url`) albo bryła A z nadrukiem ze zdjęcia
  (mechanizm pseudo-artwork jak media JU); brak pliku = goła bryła;
- B: generowana bryła z bieżących wymiarów, przerysowywana przy zmianie pól (debounce);
- wspólna skala (`refSize` = max wymiar A i B), dwuklik = zoom (mechanizm z hierarchii).

## Poza zakresem (świadomie)

- automatyczne propozycje wymiarów B (człowiek projektuje ręcznie),
- wdrażanie B do master daty / nowej instrukcji (tylko dokumentacja),
- eksport PDF/Excel dla dostawcy (można dołożyć później),
- render 3D dla wersji B inny niż generowana bryła.

## Testy

- snapshot A zamrożony mimo zmiany master daty po utworzeniu projektu;
- metryki na znanym przypadku liczbowym (+2 szt/warstwę → wyższe wypełnienie,
  mniej palet przy wolumenie);
- uprawnienia (optimizer ✓, Podgląd ✗); `accepted` blokuje edycję B;
- walidacja pliku renderu (.glb/PNG/JPG, limit rozmiaru jak przy grafikach);
- smoke render ekranu i listy + guard jednolinijkowych komentarzy `{# #}`.
