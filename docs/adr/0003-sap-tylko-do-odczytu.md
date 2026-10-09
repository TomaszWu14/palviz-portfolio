# ADR-0003: SAP tylko do odczytu

- **Status:** Przyjęta
- **Data:** 2026-07-30

## Kontekst

GROOVE potrzebuje z SAP stocku, HU i danych materiałowych, ale nie ma dostępu do API SAP —
roadmapa z 2026-07-30 zawiesza integrację real-time („na razie nie będzie dostępu do API SAP”).
Wyniki kontroli HU, optymalizacji kartonów i wycen powstają w GROOVE.

## Decyzja

SAP jest wyłącznie źródłem danych. Odczyt odbywa się przez Power BI (DAX `executeQueries`)
i importy plików (Excel/CSV, np. MARM, wsad HU). GROOVE niczego nie zapisuje ani nie księguje
w SAP: zakończenie kontroli HU to zapis wyniku w GROOVE, moduł Optymalizacja kartonów nie pisze
do SAP ani do master danych opakowań.

## Skutki

- Brak ryzyka uszkodzenia danych SAP; rozjazd stock↔hala domyka się procesem (zadania naprawcze
  dla magazynu, silnik niezgodności), nie automatyczną korektą.
- Audyt 2026-09 potwierdził brak kodu zapisującego do SAP.
- Integracja zapisująca do SAP wymaga nowego ADR.

## Źródła

- `ROADMAP_HU_ZARIA.md` (Kontrola HU, pkt 9)
- `docs/carton-opt.md:103`, `docs/procesy/kontrola-hu-mapa-procesu.md:160`
- `web/ui/powerbi.py`, `tools/sap_marm_to_palviz.py`
- Notatki wewnętrzne (audyt, backlog, planowanie, workflow wdrożeniowe) — poza publiczną wersją repozytorium.
