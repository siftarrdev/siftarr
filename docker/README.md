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

## Volumes

Compose mounts `./data:/data` relative to the `docker/` directory, so local container
state is stored under `docker/data/`.

Inside the container:

- `/data/db` stores the SQLite database by default.
- `/data/staging` is the default staging area for staged download data.

The entrypoint adjusts `/data` ownership for the runtime user before starting the app.

## Environment variables

Common variables passed by Compose:

- `TZ` for timezone, defaulting to `UTC`.
- `OVERSEERR_URL` and `OVERSEERR_API_KEY`.
- `PROWLARR_URL` and `PROWLARR_API_KEY`.
- `QBITTORRENT_URL`, `QBITTORRENT_USERNAME`, and `QBITTORRENT_PASSWORD`.
- `PLEX_URL` and `PLEX_TOKEN`.

Additional app settings can be supplied through the container environment, including
`SIFTARR_DB_PATH`, `DATABASE_URL`, `STAGING_MODE_ENABLED`, retry settings,
`MAX_EPISODE_DISCOVERY`, Plex polling intervals, and sync concurrency settings. The
entrypoint also honors `PUID` and `PGID` for runtime file ownership.

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
