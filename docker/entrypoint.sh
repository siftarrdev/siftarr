#!/bin/sh
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Adjust user/group IDs at runtime (LinuxServer-style)
if [ "$PUID" != "$(id -u appuser)" ]; then
    usermod -u "$PUID" appuser
fi
if [ "$PGID" != "$(id -g appgroup)" ]; then
    groupmod -g "$PGID" appgroup
fi

if [ -d /data ]; then
    chown -R appuser:appgroup /data
fi

exec runuser -u appuser -- "$@"