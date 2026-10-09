# ADR-0007: SQLite domyślnie, PostgreSQL przygotowany — przełącza właściciel

- **Status:** Przyjęta
- **Data:** 2026-06-21

## Kontekst

Produkcja działa na SQLite (wolumen w Coolify). Commit `91b974b` (2026-06-21) dodał obsługę
PostgreSQL przez `DATABASE_URL`; od 2026-08-08 CI testuje na PostgreSQL. Audyt (DB-001) wskazuje
ryzyko `database is locked` przy współbieżnym zapisie na SQLite.

## Decyzja

Kod obsługuje oba silniki bez zmian: puste `DATABASE_URL` = SQLite, ustawione = PostgreSQL.
Przygotowanie (serwis Postgres w compose pod profilem `pg`, checklista przełączenia — PR #568)
jest w repo, ale **samo przełączenie produkcji to osobna decyzja i wykonuje je właściciel** — nie
„przy okazji” innej zmiany. Doprecyzowanie z 2026-09-28 (M1): runbook + próba, przełącza
właściciel ([ADR-0018](0018-decyzje-procesowe-2026-09-28.md)).

## Skutki

- Lokalny dev i testy działają bez Postgresa; kod musi działać na obu silnikach (CI na PG łapie
  różnice).
- Do przełączenia obowiązują ograniczenia SQLite (jeden zapisujący naraz).

## Źródła

- `docs/postgres-switch.md`
- `web/palletweb/settings.py`, `docker-compose.yml`, `.github/workflows/ci.yml`
- PR #568, commit `91b974b`
- Notatki wewnętrzne (audyt, backlog, planowanie, workflow wdrożeniowe) — poza publiczną wersją repozytorium.
