# Compare → PalViz: eksport grafiki opakowań (spec)

Kontrakt na pliki, które **Compare** generuje z artworku (PDF/wykrojnik), a
**PalViz** wykorzystuje do budowy brył 3D opakowań.

- PalViz sam składa bryły 3D (prostopadłościany) i nakłada grafikę na ścianki.
- PalViz **nie czyta** PDF-ów ani plików 3D — dostaje płaskie obrazki per ścianka.
- Compare produkuje płaskie wycinki + opis; **nie** generuje plików 3D.

## Poziomy opakowania

Trzy poziomy, każdy traktowany tak samo:

- `karton`
- `sztuka` (opakowanie handlowe / pojedyncza jednostka)
- `opz` (opakowanie zbiorcze)

## Co Compare produkuje (dla JEDNEGO opakowania)

6 plików PNG — po jednym na każdą ściankę — oraz jeden `manifest.json`.

### Ścianki (nazwy MUSZĄ być dokładnie te — PalViz mapuje po tych kluczach)

| klucz    | ścianka   |
|----------|-----------|
| `front`  | przód     |
| `back`   | tył       |
| `left`   | lewy bok  |
| `right`  | prawy bok |
| `top`    | góra      |
| `bottom` | spód      |

### Zasady dla każdego obrazka

1. **Sama ścianka** — tylko grafika tej jednej ściany. Bez marginesów, bez
   zakładek klejowych i pól montażowych z wykrojnika.
2. **Przycięte co do krawędzi** ścianki (do linii bigu / krawędzi pudełka).
3. **Proporcje obrazka = proporcje ścianki** wg wymiarów opakowania (w cm):
   - `front`/`back` → długość × wysokość (l × h)
   - `left`/`right` → szerokość × wysokość (w × h)
   - `top`/`bottom` → długość × szerokość (l × w)

   PalViz rozciąga obrazek na całą ściankę, więc zgodna proporcja = brak zniekształceń.
4. **Orientacja „na wprost"**: grafika ustawiona tak, jak ma wyglądać, gdy
   patrzysz na tę ściankę prosto od zewnątrz (góra grafiki = góra ścianki).
5. **PNG**, tło białe lub przezroczyste. Rozdzielczość: dłuższy bok min. 1024 px.
6. Jeśli ścianka **nie ma grafiki** (np. spód czysty karton) — pomiń plik i nie
   dawaj wpisu w manifeście; PalViz zostawi tę ściankę domyślną.

## manifest.json

Obok obrazków, żeby PalViz wgrał je automatycznie:

```json
{
  "level": "karton",
  "sku": "<identyfikator/kod opakowania z master data>",
  "dims_cm": { "l": 40, "w": 30, "h": 25 },
  "faces": {
    "front":  "front.png",
    "back":   "back.png",
    "left":   "left.png",
    "right":  "right.png",
    "top":    "top.png",
    "bottom": "bottom.png"
  }
}
```

- `level`: `"karton"` | `"sztuka"` | `"opz"`.
- W `faces` tylko te ścianki, które faktycznie mają obrazek.

## Czego NIE robić

- Nie generować plików 3D (GLB/OBJ/STL) — PalViz buduje bryłę sam z obrazków.
- Nie renderować perspektywy/cieni — każdy obrazek to płaska ścianka na wprost.
- Nie zmieniać nazw kluczy ścianek (`front`/`back`/`left`/`right`/`top`/`bottom`).

## Self-check przed oddaniem

- Dla każdego obrazka: proporcja szer:wys ≈ proporcja odpowiednich wymiarów
  ścianki (tolerancja ±2%).
- `manifest.json` waliduje się jako JSON i każdy plik z `faces` istnieje.
- Żaden obrazek nie zawiera zakładek klejowych ani sąsiedniej ścianki.

## Strona PalViz (do zrobienia, gdy będą pierwsze pliki)

Import wg `manifest.json` — dziś obrazki wgrywa się ręcznie w edytorze
(`CartonArtwork` / `ProductArtwork` / `InnerPackArtwork`, kontrakt `as_dict()`).
Automatyczny import mapuje `faces` → `face` w tych modelach 1:1.
