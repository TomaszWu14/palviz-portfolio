#!/bin/sh
# PalViz backup — database + uploaded media → BACKUP_DIR, timestamped.
#
# Run from a host cron / Coolify scheduled task, e.g. nightly:
#   BACKUP_DIR=/backups DATABASE_URL="$DATABASE_URL" sh scripts/backup.sh
# then sync BACKUP_DIR off-host (rclone/rsync/object storage) — a backup on the
# same disk is not a backup. Restore procedure: docs/runbook-odtworzenie.md.
#
# Postgres  (DATABASE_URL set): pg_dump → <ts>_db.sql.gz
# SQLite    (default):          sqlite3 .backup → <ts>_db.sqlite3
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-14}"
MEDIA_ROOT="${MEDIA_ROOT:-/app/web/media}"
DB_PATH="${DB_PATH:-/app/web/db.sqlite3}"

mkdir -p "$BACKUP_DIR"

if [ -n "${DATABASE_URL:-}" ]; then
    echo "Backing up PostgreSQL → ${TS}_db.sql.gz"
    pg_dump "$DATABASE_URL" | gzip > "$BACKUP_DIR/${TS}_db.sql.gz"
else
    echo "Backing up SQLite → ${TS}_db.sqlite3"
    # .backup is safe on a live DB (consistent snapshot), unlike a raw file copy.
    sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/${TS}_db.sqlite3'"
fi

if [ -d "$MEDIA_ROOT" ]; then
    echo "Backing up media → ${TS}_media.tar.gz"
    tar -czf "$BACKUP_DIR/${TS}_media.tar.gz" -C "$(dirname "$MEDIA_ROOT")" "$(basename "$MEDIA_ROOT")"
fi

# Prune local copies older than RETAIN_DAYS (off-host copies retained separately).
find "$BACKUP_DIR" -maxdepth 1 -type f -name '*_db.*' -mtime "+$RETAIN_DAYS" -delete 2>/dev/null || true
find "$BACKUP_DIR" -maxdepth 1 -type f -name '*_media.tar.gz' -mtime "+$RETAIN_DAYS" -delete 2>/dev/null || true
echo "Backup complete → $BACKUP_DIR"
