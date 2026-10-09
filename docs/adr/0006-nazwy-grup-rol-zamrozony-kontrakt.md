# ADR-0006: Nazwy grup ról jako zamrożony kontrakt

- **Status:** Przyjęta
- **Data:** 2026-07-24

## Kontekst

Uprawnienia są grupowe (dziś 9 grup o polskich nazwach). Napisy grup są jednocześnie wierszami
`auth_group` w bazie i słownikiem, na który backend SSO mapuje claimy IdP; importuje je wiele
modułów. Zmiana napisu po cichu odbiera dostęp i psuje synchronizację SSO.

## Decyzja

Wartości-napisy grup (`"Administratorzy"`, `"Master Data"`, …) są zamrożone. Identyfikatory
Pythona wolno refaktorować, napisów nie. Wycofanie grupy = najpierw migracja wierszy
`auth_group` i mapowania claimów SSO. Nowa grupa = nowy wpis w teście kontraktu.

## Skutki

- Każda zmiana napisu łamie CI (test kontraktu pinuje dokładne wartości).
- `web/core/roles.py` jest na liście ścieżek ryzyka — PR nie scala się automatycznie
  ([ADR-0010](0010-auto-merge-claude-przez-pr.md)).
- Stary import `ui.roles` działa przez shim re-eksportujący.

## Źródła

- `web/core/roles.py`, `web/ui/roles.py`, `web/ui/oidc.py`
- `web/ui/tests/test_group_contract.py`
- `ARCHITECTURE.md` (§Zamrożone kontrakty), `.github/scripts/risky_paths.js`
- PR #234 (commit `a4ec26d`)
