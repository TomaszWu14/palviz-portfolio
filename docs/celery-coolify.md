# Celery na produkcji (Coolify)

Coolify buduje PalViz z `Dockerfile` jako **jeden kontener** — `docker-compose.yml` z repo
(z Redisem) nie jest tam używany. Dlatego worker i beat startują w kontenerze aplikacji
(`docker-entrypoint.sh`), a w Coolify jest tylko osobna baza Redis.

## Konfiguracja

| Gdzie | Co |
|---|---|
| Coolify → projekt PalViz → **+ New → Database → Redis** | `palviz-redis`, **bez publicznego portu** (sieć `coolify`, jak aplikacja) |
| Aplikacja `pal-viz` → Environment Variables | `CELERY_BROKER_URL=redis://default:<hasło>@<uuid-redisa>:6379/0` (wewnętrzny URL z karty Redisa) |
| | `CELERY_WORKER=true` — worker w tle przed gunicornem (`--concurrency ${CELERY_CONCURRENCY:-1}`) |
| | `CELERY_BEAT=true` — harmonogram (`CELERY_BEAT_SCHEDULE` w settings), plik w `/app/data` |

Po zmianie zmiennych: **Redeploy**. Wyłączenie = usunięcie `CELERY_WORKER`/`CELERY_BEAT`
(i ewentualnie `CELERY_BROKER_URL`) + Redeploy — import WT działa wtedy w wątku w tle.

## Skutki włączenia

- Działają `.delay()` (przeliczanie instrukcji, duże importy zadań EWM).
- Beat: przypomnienia SMS kierowców (tylko z `TWILIO_*`), zadania cykliczne, retencja zdjęć
  HU (`HU_PHOTO_RETAIN_DAYS`, domyślnie 30; `0` = wyłączona), retencja ZARIA
  (`retention_days` w konfiguracji ZARIA), KPI transportu; opcjonalnie raport HU, backup,
  Power BI (flagi w `settings.py`).
- Cache Django przechodzi na Redis (wspólny dla procesów gunicorna; awaria Redisa = pusty cache,
  nie błąd).

## Sprawdzenie

```sh
docker exec -w /app/web <kontener> celery -A palletweb inspect ping
docker logs <kontener> 2>&1 | grep -i "celery\|beat"
```

Ograniczenie: deploy przerywa trwające zadanie; padnięty worker restartuje pętla w
entrypoincie. Przy większym obciążeniu — osobne aplikacje Coolify dla workera i beatu.
