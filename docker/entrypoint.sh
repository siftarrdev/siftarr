#!/bin/sh
set -eu

PUID="${PUID:-568}"
PGID="${PGID:-568}"

if [ -d /data ]; then
  chown -R appuser:appgroup /data
fi

exec runuser -u appuser -- "$@"
