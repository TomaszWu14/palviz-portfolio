# syntax=docker/dockerfile:1.7
# Kolejność: od najrzadziej do najczęściej zmienianego — obraz bazowy → pakiety systemowe →
# zależności Pythona → biblioteki JS → kod → collectstatic → SHA commita (zawsze na końcu:
# Coolify podaje nowy SOURCE_COMMIT przy każdym deployu, a ARG/ENV unieważnia cache
# wszystkich instrukcji PO nim — stojąc wyżej, przebudowywał apt + venv 916 MB co deploy).
ARG PYTHON_IMAGE=python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e

# ─── Stage 1: builder — zależności Pythona w izolowanym venv ─────────────────
# Same gotowe wheele (0 pakietów budowanych ze źródeł), więc bez build-essential.
FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# uv: kilkukrotnie szybsza instalacja niż pip; te same przypięte wersje z requirements.txt.
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv

RUN python -m venv /opt/venv

# Cache pobranych paczek przeżywa buildy — zmiana requirements.txt dociąga tylko różnicę.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=requirements.txt,target=/tmp/requirements.txt \
    uv pip install -r /tmp/requirements.txt

# ─── Stage 2: vendor — biblioteki JS z CDN (three.js, htmx, alpine…) ────────
# Osobny etap zależny TYLKO od skryptu: pobieranie z CDN nie powtarza się przy każdej
# zmianie kodu (a chwilowa awaria CDN nie wywraca zwykłego deployu — incydent 2026-09-05).
FROM ${PYTHON_IMAGE} AS vendor
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /vendor/web
COPY web/scripts/fetch_vendor.sh scripts/fetch_vendor.sh
RUN sh scripts/fetch_vendor.sh

# ─── Stage 3: runtime ────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Runtime system libs only: Pango/Cairo/GDK-Pixbuf for WeasyPrint PDF rendering,
# fonts, curl/ca-certificates, and gosu (drop root → app user in the entrypoint after
# fixing volume ownership; a plain USER can't chown a root-owned named volume).
# postgresql-client: pg_dump dla nocnego backupu (ui.run_backup). Obraz bazowy = Debian
# trixie → pg_dump 17, zgodny z prod PG17 (pg_dump musi być >= wersji serwera; przy
# zmianie bazy na starszego Debiana trzeba repo PGDG + postgresql-client-17).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gosu postgresql-client \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user. The container still STARTS as root so the entrypoint can chown
# the mounted db/media volumes, then drops to this user via gosu before exec'ing gunicorn.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin app

# The fully-installed Python environment (with precompiled .pyc — faster worker boot).
COPY --from=builder /opt/venv /opt/venv

COPY --chmod=755 docker-entrypoint.sh ./
COPY palletizer/ ./palletizer/
COPY web/ ./web/
COPY --from=vendor /vendor/web/ui/static/ui/vendor/ ./web/ui/static/ui/vendor/

WORKDIR /app/web

# Collect static files at build time (vendored libs included)
RUN DJANGO_SECRET_KEY=build-only DJANGO_DEBUG=false \
    DJANGO_ALLOWED_HOSTS=* \
    python manage.py collectstatic --noinput

EXPOSE 8000

# Liveness: hit the app's DB-ping health endpoint (ui/health_urls.py).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/ || exit 1

# Wersja builda (git SHA). Coolify przekazuje SOURCE_COMMIT jako build-arg; /health/ zwraca
# to jako "version", a post-deploy smoke czeka, aż produkcja serwuje NOWY SHA (deploy.yml).
# MUSI zostać ostatnią instrukcją budującą — patrz komentarz na górze pliku.
ARG SOURCE_COMMIT=""
ENV GIT_SHA=${SOURCE_COMMIT}

ENTRYPOINT ["/app/docker-entrypoint.sh"]
