# Repository Map

Living reference for the Siftarr codebase. Keep this file committed and update it in the same change set whenever the repo structure, architectural boundaries, or primary workflows change.

## Purpose

- Give contributors and agents a fast orientation to the repo.
- Document the main runtime paths, module boundaries, and important files.
- Act as a lightweight map, not a full spec.

## How to keep this file up to date

Update `repo-map.md` whenever a change does any of the following:

- adds, removes, renames, or significantly repurposes a top-level directory
- introduces a new router, service, model group, template group, or test area
- changes the main request flow or background job flow
- changes where configuration, persistence, or integration logic lives
- adds a new contributor-critical script, doc, or operational workflow

When updating it:

1. Prefer changing this file in the same PR/commit as the structural code change.
2. Keep entries short and high-signal.
3. Describe responsibilities, not implementation trivia.
4. Remove stale entries instead of letting them drift.

## High-level architecture

Siftarr is a FastAPI application that sits between Overseerr, Prowlarr, Plex, and qBittorrent to search, score, stage, and send media releases.

Primary flow:

1. Overseerr webhook or manual action creates/syncs a request
2. Search and decision services query Prowlarr and evaluate releases
3. Winning releases are staged or sent to qBittorrent
4. Background services track retries, lifecycle state, Plex polling, and completion
5. Dashboard and settings UI expose control and visibility

## Top-level repository layout

- `app/siftarr/` — main application package
- `tests/` — automated regression and unit/integration tests
- `db/alembic/` — database migration environment and revision history
- `docker/` — container build and local container workflow
- `docs/` — cross-cutting documentation index; detailed component docs live beside code
- `data/` — locally created runtime data directory for SQLite and staging artifacts; gitignored and not committed
- `icons/` — branding assets used by docs/UI
- `README.md` — product overview and quick start
- `CONTRIBUTING.md` — developer setup, workflow, quality gates, and PR expectations
- `AGENTS.md` — repository-specific agent/development rules
- `pyproject.toml` — Python project metadata, dependencies, pytest, and Ruff config
- `ty.toml` — static type checker configuration
- `uv.lock` — locked dependency graph for `uv`

## Documentation map

- `README.md` — end-user overview, deployment, first-run setup, integrations, rules, staging, and troubleshooting
- `CONTRIBUTING.md` — developer prerequisites, local setup, dependency management, migrations, tests, quality gates, and PR workflow
- `docs/README.md` — documentation index and guidance for where detailed docs should live
- `app/siftarr/README.md` — application package boundaries, runtime flow, extension points, and package-level testing guidance
- `app/siftarr/routers/README.md` — route-layer responsibilities, extension points, and router testing guidance
- `app/siftarr/services/README.md` — service/integration responsibilities, extension points, and service testing guidance
- `app/siftarr/models/README.md` — ORM ownership, schema extension points, and persistence testing guidance
- `tests/README.md` — test organization, fixtures, async conventions, and targeted pytest commands
- `docker/README.md` — image build, Compose usage, volumes, environment variables, and helper script workflow

The old duplicated developer guide and stale product specification under `docs/` have been removed. Keep new detailed docs close to the code or workflow they describe, and keep this map as the concise orientation layer.

## Application package map

### `app/siftarr/main.py`

- FastAPI entrypoint
- logging setup
- app lifespan startup/shutdown
- router registration
- health and root endpoints

### `app/siftarr/config.py`

- application settings loading
- environment and runtime configuration access

### `app/siftarr/database.py`

- SQLAlchemy engine/session setup
- database initialization helpers

### `app/siftarr/models/`

Database entities and enums.

- `request.py` — media request state and request metadata
- `release.py` — searched/candidate releases
- `rule.py` — rule definitions for filtering/scoring
- `season.py` / `episode.py` — TV coverage and availability tracking
- `staged_torrent.py` — staged torrent persistence
- `activity_log.py` — activity/audit history
- `_base.py` — declarative base

### `app/siftarr/routers/`

HTTP route layer.

- `dashboard.py` — main dashboard page routes
- `dashboard_api.py` — dashboard JSON endpoints for details/search data
- `dashboard_actions.py` — dashboard-triggered actions and mutations
- `rules.py` — rule management UI/API
- `settings.py` — settings UI, maintenance, jobs, and connection actions
- `staged.py` — staged torrent review/approval endpoints
- `webhooks.py` — inbound webhook handling

### `app/siftarr/services/`

Business logic and integrations.

- `dashboard_service.py` — dashboard-oriented data loading and DTO assembly
- `settings_service.py` — settings persistence, maintenance, connection, and job helpers
- `request_service.py` — request creation/update orchestration
- `rule_service.py` — CRUD/order logic for rules
- `rule_engine.py` — release filtering and scoring evaluation
- `tv_decision_service.py` / `movie_decision_service.py` — media-type-specific decision flows
- `release_storage.py` — release persistence and reconstruction helpers
- `staging_actions.py` / `staging_service.py` — stage/send workflows and staged torrent handling
- `release_serializers.py` — API-facing serialization helpers
- `scheduler_service.py` / `background_tasks.py` — recurring jobs and background orchestration
- `pending_queue_service.py` / `lifecycle_service.py` / `download_completion_service.py` — retry, lifecycle, and completion transitions
- `episode_sync_service.py` / `tv_details_service.py` — TV metadata and episode synchronization helpers
- `overseerr_service.py` / `prowlarr_service.py` / `qbittorrent_service.py` — external service integrations
- `plex_service/` / `plex_polling_service.py` — Plex lookups, scans, and polling logic
- `torrent_service.py` — torrent handoff behavior
- `connection_tester.py` — external connectivity test helpers
- `http_client.py` — shared HTTP client lifecycle
- `release_parser.py`, `media_helpers.py`, `type_utils.py`, `async_utils.py` — shared parsing and utility helpers
- `activity_log_service.py` / `unreleased_service.py` — supporting domain workflows

### `app/siftarr/templates/`

Server-rendered HTML templates.

- `base.html` — shared layout
- `dashboard.html` — main dashboard UI
- `rules.html` / `rule_form.html` — rule management UI
- `settings.html` — settings UI

### `app/siftarr/static/`

Static assets.

- `css/dashboard.css` — main UI styling
- `js/dashboard*.js` and `js/dashboard/` — dashboard client-side behavior, filters, details, staged actions, and release search UX
- favicon assets

## Tests map

- `tests/routers/dashboard/` — dashboard page/API/action coverage
- `tests/routers/settings/` — settings page, connections, maintenance, and jobs coverage
- `tests/services/release_selection_service/` — release persistence/staging behavior coverage
- `tests/services/plex_service/` — Plex service unit coverage
- `tests/services/plex_polling_service/` — Plex polling flow coverage
- top-level `tests/test_*.py` — service, router, parser, config, lifecycle, and integration-focused tests

## Database and operations

- `db/alembic/env.py` — Alembic environment wiring
- `db/alembic/versions/` — single init migration only while the database is in flux; reset/stamp existing local databases when schema history is collapsed
- `docker/Dockerfile` — production/container image build
- `docker/docker-compose.yml` — local container orchestration
- `docker/rebuild-run-logs.sh` — rebuild, run, and log-tail helper
- `docker/entrypoint.sh` — container startup script

## Quality gates

Run in this order:

```bash
uv run ruff format .
uv run ruff check .
uv run ty check
uv run pytest
```

## Update checklist for structural changes

Before merging a structural change, quickly verify:

- Does `repo-map.md` still reflect the current directory/module layout?
- Are renamed or deleted modules removed here?
- Are new routers/services/tests called out here if they matter to contributors?
- Do `AGENTS.md` and `README.md` need matching updates too?
