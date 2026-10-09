# ADR-0010: Auto-merge gałęzi `claude/**` przez PR, z wyjątkami ścieżek ryzyka

- **Status:** Przyjęta
- **Data:** 2026-07-13

## Kontekst

Zmiany powstają głównie na gałęziach `claude/**`, a merge na `main` = deploy. Pierwotny
auto-merge (2026-05) scalał bezpośrednio na `main` (`merge -X theirs`), z pominięciem ochrony
gałęzi. Commit `01ecefc` (2026-07-13) przebudował CI/CD na scalanie przez PR. Audyt
(CICD-001/002) wskazał brak testu wyniku scalenia i brak przeglądu ryzykownych plików.

## Decyzja

PR z gałęzi `claude/**` po zielonym `test` dostaje natywny auto-merge GitHuba (zwykły merge
commit przez PR, token `AUTOMERGE_PAT`, by odpalił się deploy). Od PR #745 (2026-09-28): gałąź
za `main` jest najpierw aktualizowana, więc testowany jest wynik scalenia, a PR zmieniający
migracje, `web/core/roles.py`, `web/palletweb/settings.py`/`config.py`, `Dockerfile`,
`docker-entrypoint.sh` lub `.github/` nie scala się sam — dostaje komentarz i czeka na człowieka.

## Skutki

- Scalony PR `claude/**` = wdrożenie na produkcję; goły push nic nie robi.
- Zmiany ryzykowne zawsze przechodzą ręczny przegląd; lista ścieżek jest testowana w CI.

## Źródła

- `.github/workflows/ci.yml` (job `auto-merge`)
- `.github/scripts/risky_paths.js`, `.github/scripts/risky_paths.test.js`
- `CLAUDE.md` (§Git workflow)
- PR #745, commit `01ecefc`
- Notatki wewnętrzne (audyt, backlog, planowanie, workflow wdrożeniowe) — poza publiczną wersją repozytorium.
