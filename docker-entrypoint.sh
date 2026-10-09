#!/bin/sh
set -e

cd /app/web

# Writable locations: SQLite data dir + uploaded media. Create them, then (if we're
# root) hand ownership to the unprivileged `app` user so it can write to the mounted
# named volumes, which Docker creates root-owned.
mkdir -p /app/data /app/web/media

APP_USER=app
if [ "$(id -u)" = "0" ]; then
    # Default SQLite lives at /app/web/db.sqlite3 (a mounted volume, root-owned on
    # creation); chown the writable spots so the unprivileged app can write them.
    chown -R "$APP_USER":"$APP_USER" /app/data /app/web/media /app/web/db.sqlite3 2>/dev/null || true
    # DB_PATH may point elsewhere (e.g. /app/data/db.sqlite3); chown its dir too.
    if [ -n "$DB_PATH" ]; then
        chown -R "$APP_USER":"$APP_USER" "$(dirname "$DB_PATH")" 2>/dev/null || true
    fi
    RUN="gosu $APP_USER"
else
    RUN=""
fi

echo "Running migrations..."
$RUN python manage.py migrate --noinput

# Celery w tym samym kontenerze — Coolify buduje PalViz z Dockerfile (jeden kontener),
# więc worker/beat startują tu, w tle, gdy CELERY_WORKER=true (wymaga CELERY_BROKER_URL →
# Redis). Pętla podnosi padniętego workera; `|| true` — kod wyjścia 3/4 nie może zatrzymać
# gunicorna (który przejmie ten proces jako PID 1). Opis: docs/celery-coolify.md.
if [ "${CELERY_WORKER:-false}" = "true" ]; then
    BEAT=""
    if [ "${CELERY_BEAT:-false}" = "true" ]; then
        BEAT="--beat --schedule /app/data/celerybeat-schedule"
    fi
    echo "Starting Celery worker${BEAT:+ + beat} in background..."
    (
        while true; do
            $RUN celery -A palletweb worker --concurrency "${CELERY_CONCURRENCY:-1}" \
                --loglevel INFO $BEAT || true
            echo "Celery worker stopped — restarting in 5 s"
            sleep 5
        done
    ) &
fi

# Gunicorn workers: honour WEB_CONCURRENCY if set, else a sane default.
WORKERS="${WEB_CONCURRENCY:-2}"

echo "Starting gunicorn as ${APP_USER} (${WORKERS} workers)..."
exec $RUN gunicorn palletweb.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "$WORKERS" \
    --worker-class gthread \
    --threads "${GUNICORN_THREADS:-4}" \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --timeout 120 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile -
