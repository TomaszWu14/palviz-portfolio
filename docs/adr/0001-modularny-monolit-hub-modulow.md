# ADR-0001: Modularny monolit z hubem modułów

- **Status:** Przyjęta
- **Data:** 2026-06-20

## Kontekst

GROOVE obejmuje kilkanaście obszarów (paletyzacja, wyceny, Magazyn 3D, Kontrola HU, ZARIA…)
utrzymywanych przez mały zespół na jednym serwerze (Coolify/Hetzner). 2026-06-20 przez chwilę
budowano wariant „połączonych aplikacji” z SSO (commit `ea7594e`), tego samego dnia wycofany
(commit `e548a29`: „Pivot back to a single repository … no microservices/separate repos”).

## Decyzja

Jedno repo, jeden projekt Django (`palletweb`), jedna baza, jeden login. Moduły to kafelki hubu
z rejestru `MODULES` / `modules_for(user)`, bramkowane rolą. Domeny żyją w appkach
`ui`/`wh3d`/`huctl`/`transport`, wspólny rdzeń `core` nie ma modeli. Nowy obszar = wpis
w `MODULES`, nie nowe repo ani serwis.

## Skutki

- Jeden deploy, jedna migracja, brak kosztu SSO i API między serwisami.
- Granice pilnują testy: brak importów między leaf appami, rejestr modułów vs role.
- `ARCHITECTURE-MICROSERVICES.md` (2026-07-17) to niewiążąca propozycja „strangler fig” — jej
  wdrożenie wymaga nowego ADR, który zastąpi ten.

## Źródła

- `ARCHITECTURE.md` (§Hub i moduły, „Świadoma decyzja: bez mikroserwisów / osobnych repo”)
- `ARCHITECTURE-MICROSERVICES.md`
- `web/core/platform_modules.py`
- `web/ui/tests/test_module_boundaries.py`, `web/ui/tests/test_platform_modules.py`
- commity `ea7594e`, `e548a29` (2026-06-20)
