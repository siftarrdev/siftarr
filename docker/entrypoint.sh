#!/bin/sh
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
DB_PATH="${SIFTARR_DB_PATH:-/data/db/siftarr.db}"
DB_DIR=$(dirname "$DB_PATH")

# Adjust user/group IDs at runtime (LinuxServer-style)
if [ "$PUID" != "$(id -u appuser)" ]; then
    usermod -u "$PUID" appuser
fi
if [ "$PGID" != "$(id -g appuser)" ]; then
    groupmod -g "$PGID" appgroup
fi

mkdir -p "$DB_DIR" /data/staging
chown -R appuser:appgroup "$DB_DIR" /data/staging

if [ -d /data ]; then
    chown -R appuser:appgroup /data
fi

export SIFTARR_DB_PATH="$DB_PATH"

printf '[entrypoint] preparing sqlite database at %s\n' "$SIFTARR_DB_PATH"
runuser -u appuser -- /app/.venv/bin/python -c "from app.siftarr.database import prepare_sqlite_database_for_startup; prepare_sqlite_database_for_startup()"

exec runuser -u appuser -- "$@"
