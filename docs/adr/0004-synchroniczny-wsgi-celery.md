# ADR-0004: Wszystko synchroniczne (WSGI) + Celery dla długich zadań

- **Status:** Przyjęta
- **Data:** 2026-05-23

## Kontekst

Aplikacja to klasyczny Django na gunicornie (sync workers) za Coolify. Część pracy jest długa:
pełne przeliczenie instrukcji, przypomnienia dla kierowców, pobieranie z Power BI, raporty HU.
Celery wszedł commitem `c480fd7` (2026-05-23), gunicorn z wdrożeniem Docker/Coolify dzień później.

## Decyzja

Cały kod jest synchroniczny (WSGI, `palletweb.wsgi`), bez `async def`. Długie zadania idą do
Celery (broker Redis, worker + beat). Długie odpowiedzi (strumień ZARIA SSE, druk HU) to
synchroniczne generatory `StreamingHttpResponse`. W DEBUG Celery działa eager
(`CELERY_TASK_ALWAYS_EAGER = DEBUG`) — lokalnie bez Redisa i workera.

## Skutki

- Prosty model wykonania, brak mieszania sync/async w ORM.
- Każdy otwarty strumień zajmuje cały worker gunicorna — ich liczbę ogranicza `WEB_CONCURRENCY`.
- Widok `async def` wymaga najpierw przejścia na ASGI (nowy ADR).
- Na produkcji muszą działać procesy `celery worker` i `celery beat`.

## Źródła

- `CLAUDE.md` (§Conventions & gotchas)
- `web/palletweb/wsgi.py`, `web/palletweb/celery.py`, `web/palletweb/settings.py`, `web/ui/tasks.py`
- `web/ui/views/zaria_stream.py`, `web/huctl/views/hu_print.py`
- `docker-entrypoint.sh`, `docs/celery-coolify.md`
