# ADR-0009: Szybka bramka CI bez lint/mypy/skanerów bezpieczeństwa

- **Status:** Przyjęta
- **Data:** 2026-08-18

## Kontekst

Bramka PR uruchamiała ruff, mypy, bandit, pip-audit i coverage — kroki i tak nieblokujące
(informacyjne), które wydłużały merge. Ścieżka `claude/**` → auto-merge → deploy potrzebuje
szybkiego i znaczącego sygnału.

## Decyzja

Wymagany check `test` zawiera to, co chroni produkcję: bramkę 500 linii, testy `palletizer`,
`manage.py check` i testy czterech appek (na PostgreSQL), strażnik liczby testów, ruff tylko dla
star-importów oraz `check --deploy`. Skanery bezpieczeństwa (gitleaks, Trivy, pip-audit, npm)
biegną w `security.yml` (PR, main, co tydzień), CodeQL w `codeql.yml` — poza ścieżką merge.

## Skutki

- Szybszy merge; znalezisko skanera nie blokuje PR — ktoś musi czytać wyniki `security.yml`
  i `codeql.yml`.
- ruff/mypy/bandit zostają narzędziami lokalnymi (pre-commit, `requirements-dev.txt`).
- Coverage wrócił później do bramki jako próg 80% (TEST-003).

## Źródła

- `.github/workflows/ci.yml` (komentarz „SZYBKI GATE (decyzja 2026-08-18)”)
- `.github/workflows/security.yml`, `.github/workflows/codeql.yml`
- `.pre-commit-config.yaml`, `pyproject.toml`, `requirements-dev.txt`
- PR #397 (commit `b002fc5`)
