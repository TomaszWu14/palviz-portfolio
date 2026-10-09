# ADR-0013: Kontrola HU — kluczowe decyzje epiku

- **Status:** Przyjęta
- **Data:** 2026-07-30

## Kontekst

Kontrola HU to moduł krytyczny: zgodność zawartości, bramka jakości przed załadunkiem, wydajność
kontrolerów i ślad audytowy. Ustalenia zapadały w wywiadzie 30 pytań (2026-07-30), grillu 100
pytań i audycie 06 (2026-09-05) oraz w PR-ach #634–#638 — bez jednego spisu.

## Decyzja

1. Kontrola zapisuje wynik tylko w GROOVE, nic nie księguje w SAP ([ADR-0003](0003-sap-tylko-do-odczytu.md)).
2. Strefy (typy magazynu) grupujemy w procesy — 8 stref / 5 procesów (decyzja 2026-09-02), np.
   EXPORT = 92EX + WCEX; strefa jest atrybutem, przydział i KPI ją respektują.
3. Rezerwacja palety jest miękka: przejmowalna świadomym aktem z powiadomieniem, wygasa po 8 h.
4. Rozbieżność ilości: potwierdzenie „jestem pewien” (`sure=1`) zamiast wymuszonego drugiego
   przeliczenia — świadomy kompromis; rekontrolę robi inna osoba, po kolejnych porażkach eskalacja.
5. Żadna gałąź błędu nie kończy się ciszą: flaga → zgłoszenie jakościowe, rozbieżność ilości →
   zadanie naprawcze (PR #635).
6. Krótka data to miękka bramka (świadome potwierdzenie albo flaga), przeterminowane — twarda.
7. Dziury wsadu (brak partii/daty) — ostrzeżenie, nie odrzut (PR #636); „HU-duchy” — najpierw
   pomiar („Nie znaleziono palety”), naprawa przyczyn po ~2 tyg. danych.
8. Liczenie w wielu jednostkach — model addytywny (kafle = niezależne składniki), nie lustro.

## Skutki

- Obejścia (`sure=1`, wyłączona flaga wymuszenia skanu) są tolerowane świadomie — patrz
  [ADR-0012](0012-wymuszenie-skanu-bez-datawedge.md).
- Otwarte do warsztatu stref: krok po eskalacji 2× fail, SLA, sortowanie kolejki wg lokalizacji.
- Ranking kolejki i definicję krótkiego terminu doprecyzowuje [ADR-0018](0018-decyzje-procesowe-2026-09-28.md).

## Źródła

- `ROADMAP_HU_ZARIA.md`, `docs/procesy/kontrola-hu-mapa-procesu.md`
- `docs/hu-check-architektura-danych.md` (strefy, decyzja 2026-09-02)
- `web/ui/theme.py`, `web/huctl/views/hu_queue.py`, `web/huctl/views/hu_transaction_final.py`
- `web/huctl/tests/test_error_contract.py`
- Notatki wewnętrzne (audyt, backlog, planowanie, workflow wdrożeniowe) — poza publiczną wersją repozytorium.
