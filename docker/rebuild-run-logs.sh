#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

VERSION=$(git -C "$REPO_ROOT" describe --dirty --tags --always 2>/dev/null || printf '0.0.0')

printf 'Using build version: %s\n' "$VERSION"

ARBITRATARR_VERSION="$VERSION" docker compose -f "$SCRIPT_DIR/docker-compose.yml" down
ARBITRATARR_VERSION="$VERSION" docker compose -f "$SCRIPT_DIR/docker-compose.yml" build arbitratarr
ARBITRATARR_VERSION="$VERSION" docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d arbitratarr
docker compose -f "$SCRIPT_DIR/docker-compose.yml" logs -f arbitratarr
