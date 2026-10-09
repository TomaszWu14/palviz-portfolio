# ADR-0014: Zasady systemu testów regresji (P-1…P-6)

- **Status:** Przyjęta
- **Data:** 2026-09-26

## Kontekst

Etap 0 systemu testów regresji (PR #692) przełożył ogólne wymagania (spółki, „obserwowane”,
kalendarz) na realia GROOVE i zostawił sześć pytań do decyzji właściciela (P-1…P-6).

## Decyzja

- **P-1:** snapshot `/magazyn/<pk>/` jak moduł „magazyn”; przeliczenia instrukcji — Admin/MD.
- **P-2:** trasy „tylko login” zostają dostępne dla wszystkich zalogowanych.
- **P-3:** logowanie wymagane dla mediów `quality/`, `hu_control/`, `phv/`, `ewm_tasks/`.
- **P-4:** zamiast spółek — izolacja obiektów per użytkownik (ZARIA, powiadomienia, wątki,
  zadania, rezerwacje HU).
- **P-5:** „obserwowane / specjalna troska / kalendarz” — pominięte.
- **P-6:** ciężki pipeline nocą, poza godzinami pracy.
- Błędy znalezione przez testy (`BUGS-FOUND.md`) naprawiamy tylko po osobnej zgodzie właściciela.

## Skutki

- B-001…B-003 naprawione wg P-1…P-3 (PR #694); macierz uprawnień koduje docelowe zachowanie.
- P-6 w praktyce: E2E Playwright biegną nocą na `ubuntu-latest` (runner self-hosted nie ma
  przeglądarek), testy wizualne zostają lokalne.
- P-2 potwierdzone ponownie 2026-09-28 (Q-43, [ADR-0018](0018-decyzje-procesowe-2026-09-28.md)).

## Źródła

- `tests/PLAN-ETAP-0.md` (§7–8), `BUGS-FOUND.md`
- `tests/permissions.yaml`, `tests/README-TESTS.md`
- PR #692, PR #694
- Notatki wewnętrzne (audyt, backlog, planowanie, workflow wdrożeniowe) — poza publiczną wersją repozytorium.
