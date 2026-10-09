# ADR-0017: Migracje expand → contract

- **Status:** Przyjęta
- **Data:** 2026-09-28

## Kontekst

`docker-entrypoint.sh` uruchamia `migrate` przy starcie nowego kontenera, gdy stary jeszcze
obsługuje ruch. Migracja niezgodna wstecz (usunięcie lub zmiana nazwy kolumny) psuje działającą
wersję w trakcie deployu (audyt BUILD-001).

## Decyzja

Najpierw **expand** (dodaj kolumnę/model, kod przestaje używać starego), dopiero w **kolejnym**
deployu **contract** (`RemoveField`, `DeleteModel`, `RenameField`, `RenameModel`). Migracja
z operacją contract ma komentarz `# contract:` z numerem PR/deployu, który usunął użycia.
Zaaplikowanych migracji się nie edytuje — dodaje się nową.

## Skutki

- Zmiana schematu wymagająca usunięcia trwa dwa deploye.
- Migracje są na liście ścieżek ryzyka — PR scala człowiek ([ADR-0010](0010-auto-merge-claude-przez-pr.md)).
- Przy więcej niż jednej replice `migrate` trzeba przenieść do kroku pre-deploy w Coolify.

## Źródła

- `ARCHITECTURE.md` (§Konwencje strukturalne — Migracje expand → contract)
- `web/ui/tests/test_migration_contract.py`, `web/ui/tests/test_migrations_check.py`
- `docker-entrypoint.sh`
- PR #770
