#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

RAW_VERSION=$(git -C "$REPO_ROOT" describe --dirty --tags --always 2>/dev/null || printf '0.0.0')
VERSION=$(printf '%s' "$RAW_VERSION" | python -c 'import re, sys
version = sys.stdin.read().strip()
match = re.fullmatch(r"v?(\d+\.\d+\.\d+)(?:-(\d+)-g([0-9a-f]+)(-dirty)?)?", version)
if not match:
    print(version.lstrip("v"))
    raise SystemExit(0)
base, commits, sha, dirty = match.groups()
if commits is None:
    print(base)
else:
    local = f"+g{sha}"
    if dirty:
        local += ".dirty"
    print(f"{base}.dev{commits}{local}")')

printf 'Using build version: %s\n' "$VERSION"

SIFTARR_VERSION="$VERSION" docker compose -f "$SCRIPT_DIR/docker-compose.yml" down
SIFTARR_VERSION="$VERSION" docker compose -f "$SCRIPT_DIR/docker-compose.yml" build siftarr
SIFTARR_VERSION="$VERSION" docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d siftarr
docker compose -f "$SCRIPT_DIR/docker-compose.yml" logs -f siftarr
