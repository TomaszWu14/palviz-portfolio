# ADR-0012: Wymuszenie realnego skanu HU bez DataWedge

- **Status:** Przyjęta
- **Data:** 2026-08-19

## Kontekst

Kontrola HU traci sens, gdy operator „przeklikuje” palety z biurka, wpisując kody z klawiatury.
Pierwotnie rozpoznanie skanu miało opierać się na prefiksie dodawanym przez Zebra DataWedge
(`HU_SCAN_TOKEN`) skonfigurowanym na każdym urządzeniu — tę ścieżkę porzucono (audyt 06,
2026-09-05).

## Decyzja

Skan od klawiatury odróżnia JS w menu skanera (wykrywanie „burstu” znaków → `scan_src`), a każda
próba liczenia ma stempel `input_source` + `device_id` (Warstwy A i C, commit `df661ca`). Twarda
bramka `HU_SCAN_ENFORCE` (domyślnie OFF) działa w skanie online, w liczeniu pozycji i w
synchronizacji offline (PR #460). Prefiks `HU_SCAN_TOKEN` zostaje opcjonalny — do użycia dopiero,
gdy ktoś zacznie fałszować `scan_src`. Warstwa B (sekretny drugi kod na etykiecie) usunięta
(PR #481); urządzenia z aparatem mogą mieć bramkę zdjęcia palety (`HU_PHOTO_ENFORCE`, PR #482).

## Skutki

- Bramka jest anty-przypadkowa, nie anty-złośliwa: świadomy POST z `scan_src=scan` przejdzie.
- Włączenie `HU_SCAN_ENFORCE=1` na produkcji to decyzja właściciela (rekomendacja audytu: próba
  tygodniowa z monitorowaniem odbić).
- Komentarze przy flagach w konfiguracji nadal wspominają DataWedge — to ślad historyczny.

## Źródła

- `web/palletweb/config.py`, `web/huctl/views/hu_hub.py`, `web/huctl/views/hu_count.py`,
  `web/huctl/views/hu_stock.py`
- `web/huctl/tests/test_scan_enforce.py`
- PR #460, PR #481, PR #482, commit `df661ca`
- Notatki wewnętrzne (audyt, backlog, planowanie, workflow wdrożeniowe) — poza publiczną wersją repozytorium.
