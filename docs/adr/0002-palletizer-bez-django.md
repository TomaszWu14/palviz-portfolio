# ADR-0002: Pakiet `palletizer` bez zależności od Django

- **Status:** Przyjęta
- **Data:** 2026-05-02

## Kontekst

Silnik pakowania powstał jako kalkulator CLI (`main.py`) i od pierwszego commita repo
(`44bf4e4`, 2026-05-02) leży obok aplikacji `web/`, która go importuje (kalkulator warstw,
presety palet, OR-Tools, ładunek pojazdu).

## Decyzja

`palletizer/` pozostaje czystą biblioteką Pythona: geometria pakowania, dataclassy domeny
(`CartonVariant`, `PalletType`, `Dimensions` z `validate()`), IO CSV/CLI i wizualizacja —
bez żadnych importów Django. Zależności opcjonalne (OR-Tools) są importowane leniwie.
Aplikacja web używa pakietu jako lokalnej zależności (`PYTHONPATH` na korzeń repo).

## Skutki

- CLI i testy `palletizer/tests` (unittest + hypothesis) działają bez Django — osobny krok CI.
- Zmiana logiki pakowania wymaga uruchomienia obu zestawów testów (palletizer + Django).
- Regułę pilnuje test `palletizer/tests/test_framework_free.py` (AST: żaden plik pakietu nie
  importuje `django`, także w funkcjach) — uruchamiany w kroku CI biblioteki pakującej.

## Źródła

- `CLAUDE.md` (§What PalViz is, §Conventions & gotchas)
- `main.py`, `palletizer/services/pallet_calculator.py`, `palletizer/services/ortools_layer.py`
- `.github/workflows/ci.yml` (krok „Palletizer tests”)
