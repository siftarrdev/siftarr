# Docker Workflow

The Docker files provide a local container workflow and the production image used to
run Siftarr.

## Image build

Build from the repository root with the Dockerfile in this directory:

```bash
docker build -f docker/Dockerfile -t siftarr:latest .
```

The image uses `python:3.12-slim`, installs `uv`, syncs locked dependencies, copies
the app and Alembic files, and runs through `docker/entrypoint.sh`. The default
command starts Uvicorn on port `8000`.

You can pass a version label at build time:

```bash
docker build -f docker/Dockerfile --build-arg SIFTARR_VERSION=0.1.0 -t siftarr:latest .
```

## Compose usage

`docker/docker-compose.yml` builds `siftarr:latest` from the local checkout and runs a
single `siftarr` service:

```bash
docker compose -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml logs -f siftarr
docker compose -f docker/docker-compose.yml down
```

The service publishes `8000:8000` and includes a healthcheck against `/health`.
The Compose file also includes a commented example for mounting a host
`rules.json` into `/data/config/rules.json` and setting
`SIFTARR_DEFAULT_RULES_PATH` for first-run rule seeding.

## Volumes

Compose mounts `./data:/data` relative to the `docker/` directory, so local container
state is stored under `docker/data/`.

Inside the container:

- `/data/db` stores the SQLite database by default.
- `/data/db/session_secret` stores the generated browser session signing secret when
  `SECRET_KEY` is unset.
- `/data/staging` is the default staging area for staged download data.
- `/data/config/rules.json` is a recommended read-only mount location for an
  optional default rules seed file.

The entrypoint adjusts writable data directories such as `/data/db` and
`/data/staging` for the runtime user before starting the app. Read-only config
mounts under `/data/config`, such as `rules.json`, are left untouched.

## Environment variables

Common variables passed by Compose:

The bundled Compose file loads `docker/.env` with `env_file: .env`, so values in
that file are passed into the Siftarr container as runtime environment variables.

- `TZ` for timezone, defaulting to `UTC`.
- `OVERSEERR_URL` and `OVERSEERR_API_KEY`.
- `PROWLARR_URL` and `PROWLARR_API_KEY`.
- `QBITTORRENT_URL` and `QBITTORRENT_API_KEY`.
- `PLEX_URL`. `PLEX_TOKEN` is normally managed by Plex SSO after first login.
- `SECRET_KEY` to explicitly provide the Plex SSO browser session signing secret.
  When unset, Siftarr generates and persists one in the mounted data volume so
  browser sessions survive container restarts.
- `SIFTARR_SECRET_KEY_FILE` to override where that generated secret file is stored.
- `SIFTARR_API_KEY` for programmatic/webhook access; generated and persisted on
  first startup when unset.
- `SIFTARR_DEFAULT_RULES_PATH` to point at an optional mounted rules export JSON
  used only when the database has no rules.

Additional app settings can be supplied through the container environment, including
`SIFTARR_DB_PATH`, `DATABASE_URL`, `STAGING_MODE_ENABLED`, retry settings,
`MAX_EPISODE_DISCOVERY`, Plex polling intervals, and sync concurrency settings. The
entrypoint also honors `PUID` and `PGID` for runtime file ownership.

## Default rules JSON seed

To customize the rules created for a fresh database, export rules from an
existing Siftarr instance (**Rules → Export** or `/rules/export`) and save the
JSON on the host, for example `docker/rules.json`. Then enable the commented
Compose example:

```yaml
services:
  siftarr:
    volumes:
      - ./data:/data
      - ./rules.json:/data/config/rules.json:ro
    environment:
      - SIFTARR_DEFAULT_RULES_PATH=/data/config/rules.json
```

The JSON must match the version-1 import/export schema. Siftarr reads it during
startup or rule-page initialization only when the rules table is empty. Existing
rules are preserved and are not replaced by later file changes; use the Rules
import UI/API when you intentionally want to replace rules. If the path is set
but the file is missing, unreadable, or invalid, startup reports the error
instead of silently continuing without seeded rules. If no path is configured,
Siftarr starts with no rules.

## Plex SSO claim

The first browser login with Plex claims the instance and becomes the only
allowed web admin/user. Other Plex accounts are denied with a clear admin-account
message. API-key auth remains available for integrations such as webhooks.

Claim metadata and the Plex token are stored in the SQLite `app_settings` table
under `/data/db`. To reclaim after a mistaken first login, stop the container,
back up the database, remove or edit `plex_claimed_id`, `plex_username`,
`plex_thumb`, and `plex_token`, then restart and log in with the intended admin.

## Rebuild and logs helper

Use the helper from the repository root to rebuild, recreate, and optionally tail logs:

```bash
docker/rebuild-run-logs.sh
docker/rebuild-run-logs.sh --logs
```

The script derives `SIFTARR_VERSION` from Git tags/commits, runs Compose `down`,
builds the `siftarr` service, starts it detached, and tails logs when `--logs` is
provided.

Related docs: [user deployment guide](../README.md), [contributing guide](../CONTRIBUTING.md), and [repository map](../repo-map.md).
