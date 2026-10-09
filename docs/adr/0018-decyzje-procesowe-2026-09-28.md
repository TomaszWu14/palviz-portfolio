# ADR-0018: Decyzje procesowe 2026-09-28

- **Status:** Przyjęta
- **Data:** 2026-09-28

## Kontekst

Audyt 2026-09 zostawił pytania biznesowe (Q-41…Q-43) i znaleziska wymagające decyzji właściciela
(BIZ-003/005/006/007/009, SEC-003, DATA-001, M1). Właściciel rozstrzygnął je 2026-09-28;
wdrożenie idzie PR-ami #831–#839 (w dniu zapisu otwarte, jeszcze nie na `main`).

## Decyzja

1. **Statusy wysyłki automatycznie** (Q-41/BIZ-003): „Zatwierdzone” po wyborze oferty przewoźnika,
   „Wysłane” po potwierdzeniu odbioru przez kierowcę; formularz bez ręcznego wyboru statusu.
2. **Jeden ranking kolejki HU „Weź następną”** (Q-42/BIZ-005): VIP → PILNE → rekontrola → rodzina
   w toku → SLA, z filtrami snooze i zasadą „dokończ swoją”.
3. **Anulowanie wysyłki domyka proces** (BIZ-009): HU w stanie planned wypadają z kolejki, link
   kierowcy traci ważność, wybór oferty wymaga kwoty.
4. **Sync offline = te same bramki co online** (BIZ-006), a KPI oznacza wyniki offline znacznikiem.
5. **Krótki termin** = wymaganie klienta w miesiącach, bez wymagania domyślnie 6 mies.; **liczba
   palet** w wycenie liczona z wysokości wybranej na wysyłce (BIZ-007).
6. **MFA dla administratorów — odłożone** (SEC-003).
7. **Trasy „tylko login”** (osobiste i magazynowe) dostępne dla wszystkich ról; styleguide `/ui/`
   tylko dla admina (Q-43).
8. **Duplikaty nazw wysyłek** (DATA-001): ochrona w kodzie + raport `duplikaty_przesylek`;
   ograniczenie unikalności w bazie dopiero, gdy raport na produkcji pokaże 0.
9. **Przełączenie na PostgreSQL** (M1): runbook + próba; przełącza właściciel
   ([ADR-0007](0007-sqlite-domyslnie-postgres-przygotowany.md)).

## Skutki

- Pkt 5 zastępuje trzy dotychczasowe definicje krótkiego terminu i doprecyzowuje
  [ADR-0013](0013-kontrola-hu-decyzje-epiku.md); pkt 7 potwierdza P-2 ([ADR-0014](0014-zasady-testow-p1-p6.md)).
- SEC-003 pozostaje świadomie przyjętym ryzykiem do czasu nowej decyzji.

## Źródła

- decyzja właściciela 2026-09-28, PR-y #831–#839
- `tests/permissions.yaml`, `docs/postgres-switch.md`
- Notatki wewnętrzne (audyt, backlog, planowanie, workflow wdrożeniowe) — poza publiczną wersją repozytorium.
