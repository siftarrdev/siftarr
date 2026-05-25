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
2. Browser access is gated by Plex SSO; the first Plex login claims the instance as the sole admin and must finish an initial full Plex sync before reaching protected pages, while API-key auth remains for webhooks/integrations
3. Search and decision services query Prowlarr and evaluate releases (TV dashboard “Search for new” checks actionable season-pack candidates before targeted exact `SxxEyy` fallback; “Full search” refreshes all aired episode results and runs one broad TV pack query)
4. Winning releases are staged or sent to qBittorrent
5. Background services track retries, lifecycle state, Plex polling, and completion
6. Dashboard, Stats, and settings UI expose control and visibility; request details can filter/sort stored release results

## Top-level repository layout

- `app/siftarr/` — main application package
- `tests/` — automated regression and unit/integration tests
- `db/alembic/` — database migration environment and revision history
- `docker/` — container build and local container workflow
- `docs/` — cross-cutting documentation index; detailed component docs live beside code
- `data/` — locally created runtime data directory for SQLite and staging artifacts; gitignored and not committed
- `icons/` — branding assets used by docs/UI
- `README.md` — user-facing overview and Docker Compose quick start
- `CONTRIBUTING.md` — developer setup, workflow, quality gates, and PR expectations
- `AGENTS.md` — repository-specific agent/development rules
- `alembic.ini` — local Alembic CLI configuration pointing at `db/alembic/`
- `pyproject.toml` — Python 3.14 project metadata, dependencies, hatchling/hatch-vcs build backend, pytest, and Ruff config
- `ty.toml` — static type checker configuration (Python version target)
- `uv.lock` — locked dependency graph for `uv`
- `package.json` — Tailwind CSS build script and npm dev dependencies (`@tailwindcss/cli`, `tailwindcss`)
- `node_modules/` — JavaScript dependencies (gitignored)

## Documentation map

- `README.md` — user-facing overview, Docker Compose quick start, first-run setup, rules, data/configuration, and troubleshooting
- `CONTRIBUTING.md` — developer prerequisites, local setup, dependency management, migrations, tests, quality gates, and PR workflow
- `docs/README.md` — documentation index and guidance for where detailed docs should live
- `docs/stats-metrics.md` — stats metric contract, support audit, and immutable metrics persistence notes
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
- startup verifies DB readiness, initializes default rules for empty databases, then starts scheduler/background jobs and stale Overseerr/Plex catch-up syncs
- router registration
- health and root endpoints

### `app/siftarr/config.py`

- `Settings` Pydantic model loaded from environment variables
- `get_settings()` — cached singleton accessor
- `reload_settings()` — invalidates the cached singleton (called after runtime setting changes)
- first-run API key safety helpers (placeholder constant and secure key generation)
- Siftarr auto-detects `/data/config/rules.json` as a mounted Rules export JSON used only to seed an empty rules table; `SIFTARR_DEFAULT_RULES_PATH` can override this path
- `SECRET_KEY` controls browser session signing when explicitly set; otherwise Siftarr auto-generates and persists a session secret beside the SQLite DB (override path with `SIFTARR_SECRET_KEY_FILE`) so Plex SSO browser sessions survive restarts
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
- `staged_torrent.py` — staged torrent persistence; indexed on `request_id` and `status`; includes move tracking columns (`move_status`, `moved_path`, `move_error`, `moved_at`)
- `activity_log.py` — activity/audit history; indexed on `event_type`
- `stats_metrics.py` — immutable stats metric tables for selected release facts, rule outcomes, and timing events
- `app_setting.py` — key-value store for runtime-configurable settings, generated API key, and Plex SSO claim metadata (persisted across restarts)
- `_base.py` — declarative base

### `app/siftarr/routers/`

HTTP route layer.

- `auth_router.py` — Plex SSO auth endpoints (login page, first-login admin claim, initial Plex sync gate page/completion, same-admin token refresh, guarded full Plex sync kick-off after later successful admin sign-in, non-admin denial UX, logout, session info); included without global auth dependency
- `dashboard.py` — main dashboard page routes
- `dashboard_api.py` — dashboard JSON endpoints for details/search data, including validated detail-release filter/sort query controls
- `dashboard_actions.py` — dashboard-triggered actions and mutations
- `search_sse.py` — SSE streaming endpoints for live search progress; `/requests/{id}/search/stream` supports TV `search_mode=new|full`, while TV scope-specific streams remain compatibility/debug inspect paths
- `rules.py` — rule management UI/API, including unified rule listing, multi-title testing, modal create/edit actions, export, and import preview/apply flows that merge explicit keep selections from existing and imported rules
- `settings.py` — settings UI (connection test/save/reset, scheduler interval save/reset, staging toggle, Plex rescan, Overseerr sync, cache/reseed actions, SSE progress streams, API key management, Plex SSO status, non-secret settings backup preview/restore, qBit mover enable/paths/retention settings and manual trigger, and Settings-hosted background job status/manual triggers), uses SettingsStore for DB-backed persistence and keeps the SSO-managed Plex token out of connection saves/resets/backups
- `stats.py` — protected Stats page and JSON data endpoint for all-time, preset, and custom date ranges, including chart-ready time-series payloads
- `staged.py` — staged torrent review/approval endpoints; download-status endpoint now returns move tracking fields (status, path, error) for dashboard visibility
- `webhooks.py` — inbound webhook handling

### `app/siftarr/services/`

Business logic and integrations, organized into thematic subpackages:

**Flat (cross-cutting):**
- `auth_service.py` — authentication dependencies: `require_auth` (browser Plex SSO redirect with API-key fallback for programmatic requests), first-claim initial Plex sync session gate redirects, claimed-admin session validation/cleanup, request classification, `get_session_user` helper, `verify_api_key`
- `metadata_service.py` — Overseerr metadata lookup for request details
- `request_service.py` — request loading / validation
- `stats_service.py` — read-side Stats aggregation and date-range validation for cards, splits, rule outcomes, timing charts, and chart-ready time series (downloads, failures, rule rejections, indexer behavior)
- `stats_metrics_service.py` — write-only instrumentation helpers for immutable stats metric facts/events consumed by the Stats service/API

**`auth/`** — Plex SSO authentication
- `plex_oauth_service.py` — `PlexOAuthService` wrapping plex.tv API calls (PIN flow, user identity, token validation)

**`admin/`** — Config, scheduling, polling
- `settings_service.py` — SettingsStore (DB-backed settings persistence, startup API key generation, runtime env loading, sync success timestamps, Plex SSO claim/token status without exposing token, versioned non-secret settings backup/export/preview/restore), SSE progress, scheduled job helpers/status helpers, Plex rescan/Overseerr import orchestration
- `scheduler_service.py` — recurring job scheduling via APScheduler using runtime-configurable sync/completion intervals, plus startup catch-up orchestration for stale Overseerr/Plex syncs and the guarded Plex sign-in full-sync trigger used after later admin sign-ins; exposes structured scheduler/job status and manual trigger helpers for Settings; `_check_download_completion()` now also runs the qBit move/retention service; `trigger_download_completion_now()` allows manual invocation from settings with structured result reporting
- `plex_polling_service/` — Plex polling logic; prioritizes recent/downloading requests, supports explicit full reconcile, and provides targeted checks for qBit-finished plus active approved staged/downloading requests waiting on Plex availability

**`dashboard/`** — Dashboard, search, detail views
- `dashboard_service.py` — dashboard DTOs and response serializers only (load/assembly logic in sub-services)
- `detail_service.py` — request detail loading (releases, timeline, TV enrichment integration) plus stored-release title/resolution filtering, sorting, pagination counts, and applied-control metadata
- `tv_details_service.py` — TV details and sync metadata helpers
- `tv_enrichment_service.py` — TV season/episode enrichment (season data, coverage-based release grouping)
- `search_service.py` — ad hoc release evaluation/selection, request search orchestration

**`decisions/`** — Rule engine and decision pipeline
- `rule_engine.py` — release filtering and scoring evaluation; module-level rule version cache (`_rule_version`)
- `rule_service.py` — CRUD/order logic for rules, export/import validation, existing-vs-imported diff preview and selected merge/replace application, and empty-database default-rule seeding from configured `rules.json`
- `decision_pipeline.py` — shared decision pipeline helpers (rule loading, activity logging, pending queue, best-release selection)
- `tv_decision_service.py` — TV-specific decision logic (Search-for-new season-pack-first selection with exact episode fallback, Full-search broad pack evaluation, actionable staged selection)
- `movie_decision_service.py` — movie-specific decision logic

**`integrations/`** — External service adapters
- `prowlarr_service.py` — Prowlarr indexer integration; LRU search cache (45s TTL, 50 entries), TV exact-episode, broad-pack, and guarded season-search helpers
- `qbittorrent_service.py` — qBittorrent download client integration; includes idempotent single/bulk torrent add, serialized `save_path`/`seeding_time` access, listing completed torrents, `set_torrent_location()` with `move=True`, and batch delete with `delete_files=False`
- `overseerr_service.py` — Overseerr request management integration
- `connection_tester.py` — external connectivity test helpers
- `plex_service/` — Plex media server integration (lookup, scan, episode availability)

**`lifecycle/`** — Request/media lifecycle and state
- `lifecycle_service.py` — request lifecycle status transitions; 30s TTL stats cache
- `activity_log_service.py` — activity/audit logging
- `pending_queue_service.py` — retry pending queue management; supports optional `commit=False` for batched transactions
- `episode_derive.py` — canonical derivation functions for TV episode/season/request statuses
- `episode_sync_service.py` — syncing episode availability from Overseerr and Plex
- `download_completion_service.py` — completion detection via qBit torrent list matching; qBit-finished torrents stay active until targeted Plex checks confirm availability
- `qbit_move_service.py` — qBit move and retention service: selects eligible completed torrents (managed first, optional unmanaged fallback), computes safe destinations using Siftarr metadata then regex fallback, moves via qBittorrent `set_location(move=True)`, updates move tracking fields on managed torrents, performs retention cleanup (remove old completed torrents by seeding_time, keep files); called from scheduler's download-completion loop
- `overseerr_sync_service.py` — best-effort lifecycle sync back to Overseerr (approval evidence)
- `unreleased_service.py` — unreleased content handling

**`releases/`** — Release processing and staging
- `release_parser.py` — parse release names; classifies exact episodes, season/multi-season/complete-series packs, multi-episode packs; `cached_parse_release_coverage` (lru_cache, maxsize=4096)
- `release_serializers.py` — API-facing serialization helpers
- `release_storage.py` — release persistence and reconstruction helpers; `store_search_results()` scopes cleanup by search source
- `staging_service.py` — stage/send workflows, staged torrent handling, torrent download/validation, release handoff

**`utils/`** — Shared utility modules
- `http_client.py` — shared HTTP client lifecycle
- `async_utils.py` — `gather_limited` and async helpers
- `type_utils.py` — type conversion utilities
- `media_helpers.py` — media title/year extraction
- `background_tasks.py` — background orchestration (DETAILS_SYNC_TASKS)

### `app/siftarr/templates/`

Server-rendered HTML templates.

- `base.html` — shared layout (nav bar shows user avatar/name + logout when logged in)
- `dashboard.html` — main dashboard UI, including details-modal release result filters/sorting/count controls; move status badges and moved path shown in downloads table
- `login.html` — Plex SSO login page with JS-driven OAuth PIN flow, denied-admin message, and safe next redirect handling
- `initial_plex_sync.html` — first-claim setup gate that opens the full Plex sync SSE stream, shows progress/retry/logout, and unlocks protected navigation only after successful completion
- `rules.html` — single-pane rules UI with unified rule table, multi-title tester, modal create/edit wizard, export, and import preview/merge UI with existing/imported keep selections
- `rule_form.html` — fallback full-page create/edit rule form
- `settings.html` — settings UI (manual actions, connection settings with Plex SSO status and API key reveal/copy/regenerate, non-secret backup export/preview/restore, scheduler interval controls/status, background job table/manual triggers, staging toggle, qBit mover settings with enable/path/retention toggles and manual trigger)
- `stats.html` — Stats UI tab with cards, range selector, empty/error/loading states, and lightweight chart containers for summary splits and time-series trends

### `app/siftarr/static/`

Static assets.

- `css/dashboard.css` — supplemental UI styling
- `css/tailwind.css` — built Tailwind CSS output (generated, committed)
- `css/tailwind-input.css` — Tailwind CSS v4 input with CSS-based theme configuration and custom component classes
- `js/dashboard*.js` and `js/dashboard/` — dashboard client-side behavior, filters, details-modal release controls, staged actions, TV “Search for new”/“Full search” controls, movie release search UX, and SSE progress panel; polls move status fields in download-status endpoint and shows badges/paths
- `js/stats.js` — Stats API fetch/range handling and lightweight bar/time-series chart rendering
- favicon assets

## Tests map

Tests mirror the service subpackage organization under `tests/services/`:

- `tests/routers/auth/` — auth router coverage (login, plex auth, logout, session info)
- `tests/routers/dashboard/` — dashboard page/API/action coverage, including details controls and SSE search streams
- `tests/routers/settings/` — settings page, connections, maintenance, and jobs coverage
- `tests/routers/stats/` — Stats page/API coverage, including protection, range validation, and JSON payload shape
- `tests/services/auth/` — PlexOAuthService unit tests and auth_service (require_auth, get_session_user) tests
- `tests/services/admin/` — settings, scheduler, and Plex polling service tests
- `tests/services/decisions/` — rule engine, rule service (including import/export-backed default seeding), TV/movie decision service tests
- `tests/services/integrations/` — Prowlarr, qBittorrent, Overseerr, Plex service tests
- `tests/services/lifecycle/` — lifecycle, activity log, episode sync, download completion, qbit move service tests
- `tests/services/test_stats_service.py` — Stats aggregation/range unit tests
- `tests/services/releases/` — release parser, serializers, staging, and release selection tests
- `tests/services/utils/` — type utils tests
- Top-level `tests/test_*.py` — integration tests (season sweep, torrent helpers, API, router-level, config)

## CI/CD Workflows

- `.github/workflows/ci.yml` — quality gates (lint, format, typecheck, tests, Docker build) on push/PR to main/develop
- `.github/workflows/deploy.yml` — build and push Docker image to GHCR on `v*` tags
- `.github/workflows/release.yaml` — create GitHub Release with built Python package artifacts and auto-generated release notes on `v*` tags

## Database and operations

- `alembic.ini` / `db/alembic.ini` — local and container Alembic CLI configuration
- `db/alembic/env.py` — Alembic environment wiring
- `db/alembic/versions/` — compact schema migrations; reset/stamp existing local databases when schema history is collapsed
- container startup runs Alembic/SQLite repair before the FastAPI app launches; FastAPI startup then seeds default rules for empty databases
- `docker/Dockerfile` — multi-stage production image build (Node stage builds Tailwind CSS, Python stage runs the app)
- `docker/docker-compose.yml` — local container orchestration with `docker/siftarr-rules.json` mounted for empty-database rule seeding
- `docker/rebuild-run-logs.sh` — rebuild, run, and log-tail helper (use when deps change)
- `docker/dev-up.sh` — fast dev loop with volume mounts and uvicorn --reload (daily use, no rebuild)
- `docker/docker-compose.override.yml` — dev overrides: source code volume mounts and `--reload` command
- `docker/entrypoint.sh` — container startup script

## Tailwind CSS Build

Tailwind CSS v4 is built with `@tailwindcss/cli`. Configuration lives in
`app/siftarr/static/css/tailwind-input.css` using CSS-based `@theme` settings;
there is no `tailwind.config.js`.

After changing custom styles, Tailwind theme values, or Tailwind classes in
templates, rebuild the CSS:

```bash
npm run build:css
```

The Docker multi-stage build rebuilds Tailwind CSS automatically.

### Dev mode

When running via `docker/dev-up.sh`, the app container mounts the host
`app/` directory and uses `uvicorn --reload` for instant restart on
Python/template changes. For Tailwind CSS changes, run a full Docker
rebuild (`docker/rebuild-run-logs.sh`).

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
