# Rejestr decyzji architektonicznych i procesowych (ADR)

ADR (Architecture Decision Record) to krótki zapis **jednej świadomej decyzji**: jaki problem ją
wymusił, co postanowiono i z jakimi skutkami. Rejestr odpowiada na pytanie „czy to jest decyzja,
czy zaległość?”, zanim ktoś ją cofnie. Obejmuje decyzje architektoniczne (np. monolit, WSGI),
infrastrukturalne (CI, baza) i procesowe (reguły Kontroli HU, statusy wysyłek).

Dokumenty analityczne i feasibility (np. [`ollama-feasibility.md`](../ollama-feasibility.md)) żyją
w `docs/*.md`; wiążąca decyzja, która z nich wynika, trafia tutaj jako ADR.

## Zasady

- Nowa świadoma decyzja = nowy plik `NNNN-krotki-tytul.md` (kolejny numer, 4 cyfry, nazwa ASCII
  kebab-case) + wiersz w indeksie poniżej.
- Decyzji **nie edytuje się wstecz**. Zmiana decyzji = nowy ADR; w starym zmienia się tylko linię
  statusu na „Zastąpiona przez ADR-NNNN” (i wiersz w indeksie).
- Statusy: `Proponowana`, `Przyjęta`, `Odrzucona`, `Wycofana`, `Zastąpiona przez ADR-NNNN`.
- Data = dzień podjęcia decyzji; gdy nieznana — data pierwszego commita, który ją wprowadził.
- Źródła podawaj jako ścieżki względem korzenia repo w backtickach (np. `docs/carton-opt.md:103`)
  albo względne linki markdown, plus numery PR / commity. Tylko fakty z repo.
- Wpis krótki: ok. 10–30 linii treści; decyzję zbiorczą (kilka ustaleń z jednego dnia lub
  epiku) zapisz jako numerowane punkty.

Strażnik: `web/ui/tests/test_docs_runbooks.py` (klasa `AdrRegisterTests`) sprawdza sekcje, linię
statusu, obecność w indeksie, ciągłość numeracji i istnienie ścieżek z sekcji „Źródła”.

## Szablon

```markdown
# ADR-NNNN: Tytuł decyzji

- **Status:** Przyjęta
- **Data:** RRRR-MM-DD

## Kontekst
Jaki problem / ograniczenie wymusza decyzję.

## Decyzja
Co postanowiono (jedno zdanie + szczegóły). Opcjonalnie: odrzucone alternatywy.

## Skutki
Co zyskujemy, co tracimy, co trzeba pilnować (test / strażnik CI).

## Źródła
- `sciezka/w/repo.md`, PR #NNN, commit `abc1234`
```

## Indeks

| Nr | Tytuł | Status | Data |
|---|---|---|---|
| [0001](0001-modularny-monolit-hub-modulow.md) | Modularny monolit z hubem modułów | Przyjęta | 2026-06-20 |
| [0002](0002-palletizer-bez-django.md) | Pakiet `palletizer` bez zależności od Django | Przyjęta | 2026-05-02 |
| [0003](0003-sap-tylko-do-odczytu.md) | SAP tylko do odczytu | Przyjęta | 2026-07-30 |
| [0004](0004-synchroniczny-wsgi-celery.md) | Wszystko synchroniczne (WSGI) + Celery dla długich zadań | Przyjęta | 2026-05-23 |
| [0005](0005-vendorowane-biblioteki-bez-cdn.md) | Vendorowane biblioteki front-end, brak CDN w runtime | Przyjęta | 2026-06-17 |
| [0006](0006-nazwy-grup-rol-zamrozony-kontrakt.md) | Nazwy grup ról jako zamrożony kontrakt | Przyjęta | 2026-07-24 |
| [0007](0007-sqlite-domyslnie-postgres-przygotowany.md) | SQLite domyślnie, PostgreSQL przygotowany — przełącza właściciel | Przyjęta | 2026-06-21 |
| [0008](0008-csp-report-only.md) | CSP w trybie report-only | Przyjęta | 2026-07-02 |
| [0009](0009-szybka-bramka-ci.md) | Szybka bramka CI bez lint/mypy/skanerów bezpieczeństwa | Przyjęta | 2026-08-18 |
| [0010](0010-auto-merge-claude-przez-pr.md) | Auto-merge gałęzi `claude/**` przez PR, z wyjątkami ścieżek ryzyka | Przyjęta | 2026-07-13 |
| [0011](0011-ci-na-self-hosted-runnerze.md) | CI i deploy na własnym runnerze (self-hosted) | Przyjęta | 2026-09-25 |
| [0012](0012-wymuszenie-skanu-bez-datawedge.md) | Wymuszenie realnego skanu HU bez DataWedge | Przyjęta | 2026-08-19 |
| [0013](0013-kontrola-hu-decyzje-epiku.md) | Kontrola HU — kluczowe decyzje epiku | Przyjęta | 2026-07-30 |
| [0014](0014-zasady-testow-p1-p6.md) | Zasady systemu testów regresji (P-1…P-6) | Przyjęta | 2026-09-26 |
| [0015](0015-zaria-odczyt-prywatnosc-modele.md) | ZARIA — tylko odczyt, prywatne rozmowy, model lokalny | Przyjęta | 2026-07-30 |
| [0016](0016-nawigacja-launcher-ctrl-k.md) | Nawigacja — launcher Ctrl+K zamiast grupowania paska | Przyjęta | 2026-08-08 |
| [0017](0017-migracje-expand-contract.md) | Migracje expand → contract | Przyjęta | 2026-09-28 |
| [0018](0018-decyzje-procesowe-2026-09-28.md) | Decyzje procesowe 2026-09-28 | Przyjęta | 2026-09-28 |
