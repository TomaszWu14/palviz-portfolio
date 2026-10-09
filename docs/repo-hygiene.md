# Higiena repo — reguła rozstrzygania nietrackowanych artefaktów

Cel: gdy w `git status` pojawia się nowy nietrackowany plik/katalog, ta tabela
mówi od razu commit / ignore / delete — bez odkrywania reguły na nowo.

## Tabela reguły

| Kategoria artefaktu | Decyzja | Uzasadnienie |
|---|---|---|
| `.planning/**` (intel, codebase, onboarding, plany) | commit | Zapisana historia ustaleń projektu; całe `.planning/` jest commitowane by design |
| `.scratch/<temat>/**` (np. `concerns-cleanup/`, `hu-control-wsad/`) | commit | Realny backlog ticketów/notatek roboczych — wzorzec ustalony w `.scratch/hu-control-wsad/issues/*.md` |
| `docs/**` | commit | Dokumentacja projektu/procesów |
| `graphify-out/graph.*`, `GRAPH_REPORT.md`, `manifest.json` (top-level) | commit | Kanoniczny graf wiedzy, pilnowany przez `graph-freshness.yml` |
| `graphify-out/RRRR-MM-DD/` (migawki backupu grafu) | ignore | Regenerowalne przez `graphify update .` przy każdym rebuildzie; zaśmiecają status bez wartości historycznej |
| Lokalne wypełnione dane testowe (np. `excel_templates/GROOVE_userzy_testowi.xlsx`) | ignore | To dane (loginy testowe), nie wzór — wzory (`wzor_import_*`, `GROOVE_ankieta_*`) zostają śledzone; ignorujemy dokładną ścieżkę, nie szeroki glob |
| Output generowany (`output/`, `cache/`, `.graphify_python`, itp.) | ignore | Odtwarzalne przy każdym uruchomieniu narzędzia |

## Granica bezpieczeństwa

Usuwanie z working tree tylko jawnym, wyliczonym `rm -rf` na konkretnych,
zweryfikowanych jako nietrackowane ścieżkach (`git status --porcelain` →
linie `??`). Nigdy `git clean -fdx` ani `git rm` na plikach, które są już
śledzone — to nieodwracalnie kasuje historię.

## Cykl życia `.planning/`

Reguła (nie migawka stanu) — co jest trwałe przez cały milestone, co się
akumuluje, co jest zamrożonym logiem:

| Ścieżka | Status | Reguła |
|---|---|---|
| `.planning/PROJECT.md`, `ROADMAP.md`, `REQUIREMENTS.md`, `STATE.md` | commit | Zawsze commitowane, trwałe przez cały cykl życia milestone'u — jedno źródło prawdy o stanie projektu |
| `.planning/phases/NN-nazwa/` | commit | Jeden katalog na fazę (CONTEXT.md, RESEARCH.md, PLAN.md, SUMMARY.md, …), akumuluje się w trakcie milestone'u — nie usuwać, dopóki milestone trwa |
| `.planning/codebase/` | commit | Auto-generowana mapa kodu (`gsd-map-codebase`), odświeżana narzędziowo — nie edytować ręcznie |
| `.planning/intel/` | commit | Zamrożony log ustaleń klasyfikatora dokumentów z onboardingu — traktować jako historię, nie żywy dokument |
| `.planning/onboarding/` | commit | Jednorazowy snapshot wdrożenia GSD do repo |

Po zamknięciu milestone'u fazy porządkuje narzędziowo `gsd-complete-milestone` /
`gsd-cleanup` — `.planning/phases/` rośnie przez cały milestone i jest
archiwizowana/sprzątana automatycznie po jego zamknięciu, nie ręcznie w trakcie.
