# ADR-0016: Nawigacja — launcher Ctrl+K zamiast grupowania paska

- **Status:** Przyjęta
- **Data:** 2026-08-08

## Kontekst

Pasek GROOVE z kilkunastoma modułami w jednym rzędzie stał się nieczytelny, zwłaszcza na węższych
ekranach. Rozważono grupowanie rozwijane (Magazyn / Transport / Dane / Narzędzia) i paletę poleceń.

## Decyzja

Launcher Ctrl+K / Cmd+K (paleta poleceń) z przyciskiem „Ctrl K” w pasku dla myszy i tabletu.
„Ostatnio używane” trzymane w `localStorage` (bez backendu); dane launchera z `nav_modules` — to
samo źródło co pasek. Grupowanie rozwijane odrzucone; może wrócić wyłącznie jako uzupełnienie paska.

## Skutki

- Nowy moduł z rejestru pojawia się w launcherze automatycznie, bez taksonomii grup.
- Odłożone: breadcrumb, ulubione/przypięte, grupowanie tematyczne paska.

## Źródła

- `docs/ui-audit/etap3-nawigacja.md` (pierwotny dokument decyzyjny)
- `web/core/context_processors.py`
- PR #285
- Notatki wewnętrzne (audyt, backlog, planowanie, workflow wdrożeniowe) — poza publiczną wersją repozytorium.
