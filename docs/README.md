# Mapa dokumentacji — typ dokumentu → katalog

Cel: gdy nie wiesz gdzie coś napisać (albo gdzie czegoś szukać), ta tabela mówi
od razu — bez zgadywania.

| Typ dokumentu | Katalog docelowy | Uwaga |
|---|---|---|
| Architektura platformy (żywa, ręcznie pisana) | root [`ARCHITECTURE.md`](../ARCHITECTURE.md) | Hub/moduły, marka, konwencje strukturalne — aktualizowany przy zmianie modułów/konwencji |
| Mapa kodu (auto-generowana) | [`.planning/codebase/`](../.planning/codebase/) — w tym jej **własny** `.planning/codebase/ARCHITECTURE.md` | **To NIE jest ten sam plik co root `ARCHITECTURE.md`.** Generowany przez `gsd-map-codebase`, snapshot struktury/diagram ASCII — nie edytować ręcznie, nie traktować jako kanon |
| ADR / decyzja | [`docs/adr/`](adr/README.md) — pliki `NNNN-krotki-tytul.md` | Jedna świadoma decyzja = jeden plik (kontekst/decyzja/skutki/źródła) + wiersz w indeksie; nie edytuje się wstecz — zastępuje nowym ADR |
| Analiza / feasibility | `docs/*.md` (np. `ollama-feasibility.md`) | Dokument analityczny z wariantami i rekomendacją; wiążąca decyzja z niego trafia do `docs/adr/` |
| Spec / kontrakt integracji | `docs/*.md` (np. `compare-export-spec.md`) | Kontrakty międzysystemowe |
| Setup / procedura IT | `docs/*.md` (np. `outlook-setup.md`, `zaria-server-setup.md`, `postgres-switch.md`, `blender-mcp-setup.md`) | Konfiguracja infrastruktury |
| Runbooki operacyjne i przekazanie | [`runbook-operatora.md`](runbook-operatora.md), [`runbook-odtworzenie.md`](runbook-odtworzenie.md) | Utrzymanie bez głównego developera (w tym rollback), odtworzenie po awarii |
| Mapa procesu biznesowego | `docs/procesy/` | Dla pracowników magazynu, nie deweloperów |
| Audyt UI/UX | `docs/ui-audit/` | Migawka stanu w czasie, nie żywa specyfikacja |
| Archiwum wdrożonych planów/speców | `docs/archive/` | Historyczne plany/specy już zrealizowanych featurów (patrz `docs/archive/README.md` — dowód wdrożenia per plik) |
| Higiena repo / cykl życia artefaktów | [`docs/repo-hygiene.md`](repo-hygiene.md) | Reguła commit/ignore/delete + cykl życia `.planning/` |
| Ustalenia projektu GSD | `.planning/**` (PROJECT.md, ROADMAP.md, REQUIREMENTS.md, STATE.md, phases/, intel/, onboarding/) | Commitowane by design |
| Backlog roboczy per temat | `.scratch/<temat>/issues/*.md` | Ustalony precedens (Faza 1) — commitowany, nie ignorowany |

## Kolizja nazw: dwa pliki `ARCHITECTURE.md`

Root [`ARCHITECTURE.md`](../ARCHITECTURE.md) i `.planning/codebase/ARCHITECTURE.md`
to **dwa różne pliki**, różne cele:

- **Root `ARCHITECTURE.md`** — ręcznie pisany, żywy dokument opisujący hub/moduły/markę
  i konwencje strukturalne. To jest kanon.
- **`.planning/codebase/ARCHITECTURE.md`** — wygenerowany przez `gsd-map-codebase`,
  snapshot mapy kodu (diagram ASCII systemu). Odświeżany narzędziowo, nie edytowany
  ręcznie, nie jest kanonem.

Nie scalać, nie usuwać żadnego z nich — oba mają swój cel.
