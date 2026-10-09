# ADR-0011: CI i deploy na własnym runnerze (self-hosted)

- **Status:** Przyjęta
- **Data:** 2026-09-25

## Kontekst

Minuty GitHub Actions w prywatnym repo kosztują i podlegają limitowi wydatków. Pierwsza próba
runnera (PR #500, 2026-08-23) skończyła się powrotem na `ubuntu-latest` po odblokowaniu minut
(commit `e65f11e`, 2026-09-01); runner postawiono ponownie 2026-09-25 (PR #674).

## Decyzja

Joby `test` i `auto-merge` (`ci.yml`) oraz deploy (`deploy.yml`) biegną na runnerze
`hetzner-gha-palviz` — tym samym serwerze co Coolify. Runner świadomie nie ma dostępu do Dockera
(grupa `docker` = root na hoście z produkcją); Postgres do testów to stały kontener
`palviz-ci-postgres` na 127.0.0.1. `security.yml`, `codeql.yml`, `graph-freshness.yml` i nocne E2E
zostają na `ubuntu-latest`.

## Skutki

- 0 minut Actions na ścieżce merge i deployu.
- CI dzieli host z produkcją: awaria serwera zatrzymuje oba; cięższe przebiegi — nocą (P-6).
- Runner nie ma przeglądarek, więc E2E Playwright biegną na hostowanym runnerze.
- Odtworzenie runnera i bazy CI opisuje runbook wewnętrzny (poza publiczną wersją repozytorium).

## Źródła

- `.github/workflows/ci.yml`
- `tests/PLAN-ETAP-0.md` (P-6)
- PR #500, PR #674, commit `e65f11e`
- Notatki wewnętrzne (audyt, backlog, planowanie, workflow wdrożeniowe) — poza publiczną wersją repozytorium.
