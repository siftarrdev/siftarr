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
- `package.json` / `tailwind.config.js` — Tailwind CSS build config (npm dev dependency)
- `node_modules/` — JavaScript dependencies (gitignored)

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
- startup verifies DB readiness before scheduler/background jobs start
- router registration
- health and root endpoints

### `app/siftarr/config.py`

- `Settings` Pydantic model loaded from environment variables
- `get_settings()` — cached singleton accessor
- `reload_settings()` — invalidates the cached singleton (called after runtime setting changes)
- `get_static_version()` — cache-busting value for static assets

### `app/siftarr/version.py`

- runtime version string derived from git tags or package metadata

### `app/siftarr/database.py`

- SQLAlchemy engine/session setup
- SQLite startup repair helpers used by container boot
- app-side database readiness verification (does not create schema directly)

### `app/siftarr/models/`

Database entities and enums.

- `request.py` — media request state and request metadata
- `release.py` — searched/candidate releases
- `rule.py` — rule definitions for filtering/scoring
- `season.py` / `episode.py` — TV coverage and availability tracking
- `staged_torrent.py` — staged torrent persistence; indexed on `request_id` and `status`
- `activity_log.py` — activity/audit history; indexed on `event_type`
- `app_setting.py` — key-value store for runtime-configurable settings (persisted across restarts)
- `_base.py` — declarative base

### `app/siftarr/routers/`

HTTP route layer.

- `dashboard.py` — main dashboard page routes
- `dashboard_api.py` — dashboard JSON endpoints for details/search data
- `dashboard_actions.py` — dashboard-triggered actions and mutations
- `search_sse.py` — SSE streaming endpoints for live search progress and TV inspect results
- `rules.py` — rule management UI/API, including unified rule listing, multi-title testing, modal import/export, and create/edit actions
- `settings.py` — settings UI (connection test/save/reset, staging toggle, Plex rescan, Overseerr sync, cache/reseed actions, SSE progress streams), uses SettingsStore for DB-backed persistence
- `staged.py` — staged torrent review/approval endpoints
- `webhooks.py` — inbound webhook handling

### `app/siftarr/services/`

Business logic and integrations.

- `dashboard_service.py` — dashboard DTOs and response serializers only (load/assembly logic moved to sub-services)
- `detail_service.py` — request detail loading (releases, timeline, TV enrichment integration)
- `tv_enrichment_service.py` — TV season/episode enrichment (season data, release grouping, metadata)
- `metadata_service.py` — Overseerr metadata lookup for request details
- `settings_service.py` — SettingsStore (DB-backed settings persistence), SSE progress, scheduled job helpers, Plex rescan/Overseerr import orchestration
- `request_service.py` — request creation/update orchestration
- `rule_service.py` — CRUD/order logic for rules
- `rule_engine.py` — release filtering and scoring evaluation; includes module-level rule version cache (`_rule_version`) for rule engine reuse across requests, invalidated on rule mutations
- `decision_pipeline.py` — shared decision pipeline helpers (rule loading, activity logging, pending queue, best-release selection); accepts optional cached `RuleEngine` to skip DB load
- `release_storage.py` — release persistence and reconstruction helpers; now uses upsert/diff approach in `store_search_results()` to avoid full-table churn on re-search
- `staging_service.py` — stage/send workflows, staged torrent handling, torrent download/validation, and release handoff (`use_releases`); uses shared HTTP client for torrent downloads
- `release_serializers.py` — API-facing serialization helpers
- `scheduler_service.py` / `background_tasks.py` — recurring jobs and background orchestration
- `pending_queue_service.py` / `lifecycle_service.py` / `download_completion_service.py` — retry, status transitions, and completion detection (unreleased detection moved to unreleased_service); pending queue methods support optional `commit=False` for batched transactions; completion service fetches qBit torrent list once per cycle for local matching; lifecycle stats cached with 30s TTL
- `episode_sync_service.py` / `tv_details_service.py` — TV metadata and episode synchronization helpers
- `overseerr_service.py` / `prowlarr_service.py` / `qbittorrent_service.py` — external service integrations; Prowlarr service includes LRU search result cache (45s TTL, 50 entries, disable via `SIFTARR_DISABLE_SEARCH_CACHE`), concurrent broad TV searches via `asyncio.gather`, and cache invalidation on rule changes
- `plex_service/` / `plex_polling_service.py` — Plex lookups, scans, and polling logic; polling prioritizes recent/downloading requests with periodic full reconcile every 20th poll cycle
- `connection_tester.py` — external connectivity test helpers
- `http_client.py` — shared HTTP client lifecycle
- `release_parser.py`, `media_helpers.py`, `type_utils.py`, `async_utils.py` — shared parsing and utility helpers; `release_parser` includes `cached_parse_release_coverage` (lru_cache, maxsize=4096) to avoid redundant coverage parsing
- `episode_derive.py` — canonical derivation functions for TV episode/season/request statuses (episode status is ground truth for TV)
- `activity_log_service.py` / `unreleased_service.py` — supporting domain workflows (unreleased detection moved here from lifecycle_service)
- `search_service.py` — ad hoc release evaluation/selection, request search orchestration, and TV season-pack/episode ad hoc search

### `app/siftarr/templates/`

Server-rendered HTML templates.

- `base.html` — shared layout
- `dashboard.html` — main dashboard UI
- `rules.html` — single-pane rules UI with unified rule table, multi-title tester, modal create/edit wizard, and modal import/export
- `rule_form.html` — fallback full-page create/edit rule form
- `settings.html` — settings UI

### `app/siftarr/static/`

Static assets.

- `css/dashboard.css` — supplemental UI styling
- `css/tailwind.css` — built Tailwind CSS output (generated, committed)
- `css/tailwind-input.css` — Tailwind CSS input with `@tailwind` directives and custom component classes
- `js/dashboard*.js` and `js/dashboard/` — dashboard client-side behavior, filters, details, staged actions, release search UX, and SSE progress panel
- favicon assets

## Tests map

- `tests/routers/dashboard/` — dashboard page/API/action coverage, including SSE search streams
- `tests/routers/settings/` — settings page, connections, maintenance, and jobs coverage
- `tests/services/release_selection_service/` — release persistence/staging behavior coverage
- `tests/services/plex_service/` — Plex service unit coverage
- `tests/services/plex_polling_service/` — Plex polling flow coverage
- `tests/test_rules_router.py` / `tests/test_rule_engine.py` / `tests/test_rule_service.py` — rules UI/API, evaluation, and import/export coverage
- top-level `tests/test_*.py` — service, router, parser, config, lifecycle, and integration-focused tests

## Database and operations

- `db/alembic/env.py` — Alembic environment wiring
- `db/alembic/versions/` — single init migration only while the database is in flux; reset/stamp existing local databases when schema history is collapsed
- container startup runs Alembic/SQLite repair before the FastAPI app launches
- `docker/Dockerfile` — multi-stage production image build (Node stage builds Tailwind CSS, Python stage runs the app)
- `docker/docker-compose.yml` — local container orchestration
- `docker/rebuild-run-logs.sh` — rebuild, run, and log-tail helper (use when deps change)
- `docker/dev-up.sh` — fast dev loop with volume mounts, uvicorn --reload, and tailwind watcher (daily use, no rebuild)
- `docker/docker-compose.override.yml` — dev overrides: source code volume mounts, `--reload` command, tailwind-watcher sidecar
- `docker/entrypoint.sh` — container startup script

## Tailwind CSS Build

After changing custom styles or Tailwind classes in templates, rebuild the CSS:

```bash
npm run build:css
```

The Docker multi-stage build rebuilds Tailwind CSS automatically.

### Dev mode watcher

When running via `docker/dev-up.sh`, a `tailwind-watcher` sidecar container
(node:20-slim) runs `npx tailwindcss --watch` and regenerates `tailwind.css`
on template changes. No local Node.js or manual rebuild needed during
development. Just refresh your browser after saving a template.

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
