# Siftarr Improvement Plan
## Executive Summary
This plan identifies **30+ concrete improvement areas** across three categories: **outdated/unused/incomplete code paths**, **overly complex features needing simplification**, and **database migration cleanup**. Each item includes the problem, impact, and recommended action.
---
## Category A: Outdated, Unused, or Not Fully Implemented Code Paths
### A1. Root Redirect Loop in `main.py`
- **Location:** `app/siftarr/main.py:299-302`
- **Problem:** The root endpoint returns `RedirectResponse(url="/")`, which redirects to itself. This creates an infinite redirect loop that is only masked by FastAPI's static file routing.
- **Impact:** Buggy behavior, potential browser redirect errors, incorrect health checks.
- **Action:** Change to `RedirectResponse(url="/dashboard")` or remove the endpoint and let static files handle `/`.
### A2. Empty `finally: pass` Cleanup Blocks
- **Location:** `app/siftarr/routers/webhooks.py:220`, `app/siftarr/routers/dashboard_api.py:584,647,697`
- **Problem:** Service clients (Prowlarr, qBittorrent, Overseerr) are created but never explicitly closed on exceptions because `finally` blocks contain only `pass`.
- **Impact:** Resource leaks (HTTP connections), brittle exception handling.
- **Action:** Replace `finally: pass` with proper `try/finally` or context managers that close clients.
### A3. TV General Search Button (Not Fully Implemented)
- **Location:** Dashboard UI / `dashboard_api.py`
- **Problem:** Per `TODO.md`, the general TV search button in the details modal shows a "not fully implemented" toast. Users cannot search for specific seasons, episodes, or multi-season packs from the UI. The planned redesign needs to support different search strategies depending on context: searching for a specific season, individual episodes, or multi-season packs. The UI should let the user choose what scope to search (e.g., "Search Season X", "Search All Episodes", "Search Multi-Season Packs") rather than a single generic search button.
- **Impact:** Core feature incomplete, poor UX for TV requests.
- **Action:** Either implement the redesigned search (season/episode/pack scope selection) or remove the button and rely on auto-search until ready.
### A4. Planned Features from TODO.md (Context Only)
The following features are documented in `TODO.md` but have no implementation. They are noted here for context but are **not recommended for immediate action**:
- **User/Group-Scoped Rules** (`TODO.md` line 1): "Add option to only apply rules to requests from certain users or groups." This is a future enhancement that requires UI design and rule engine changes. Keep in `TODO.md` as a backlog item.
- **Indexer Preferences** (`TODO.md` line 2): "Add indexer and release-source preferences, including allow/deny lists and weighted priorities." Note: the current rule engine already supports exclusions by indexer name via regex. A dedicated indexer preference system would be a future enhancement.
### A5. Legacy SQLite Compatibility Hooks in `main.py`
- **Location:** `app/siftarr/main.py:79-207`
- **Problem:** Five separate functions (`_prepare_legacy_sqlite_database_for_migrations`, `_repair_missing_alembic_revision`, `_ensure_request_rejection_reason_column`, `_ensure_staged_torrents_selection_source_column`, `_ensure_sqlite_requeststatus_allows_unreleased`) exist to patch databases that predate the current schema. These are workaround piles for a pre-1.0 app.
- **Impact:** 130 lines of startup complexity, fragile subprocess calls to `alembic stamp`, confusing startup logic.
- **Action:** Delete all compatibility hooks. Since the project is pre-stable, document that users should delete and recreate the database on schema changes.
### A6. Dead Code: `_poll_plex_availability` Compatibility Wrapper
- **Location:** `app/siftarr/services/scheduler_service.py:164-166`
- **Problem:** A wrapper method that simply delegates to `_run_incremental_plex_sync_job`. Nothing calls it.
- **Impact:** Dead code, misleading naming.
- **Action:** Delete the method.
### A7. `repair_request_state.py` CLI Utility
- **Location:** `app/siftarr/repair_request_state.py`
- **Problem:** A standalone CLI script for repairing request states. Likely unused and unmaintained given the existence of `LifecycleService`.
- **Impact:** Orphaned code file.
- **Action:** Delete. If CLI repair is needed, build it as a proper Click/Typer command integrated with the app.
### A8. Unused `Release.is_downloaded` / `downloaded_at` Tracking
- **Location:** `app/siftarr/models/release.py`
- **Problem:** Releases track `is_downloaded` and `downloaded_at`, but the actual download tracking happens via `StagedTorrent` and qBittorrent. The fields are partially updated in `release_selection_service.py` but not reliably used anywhere.
- **Impact:** Schema clutter, confusing data model.
- **Action:** Remove `is_downloaded` and `downloaded_at` from the `Release` model. Simplify `use_releases()` to not mutate these fields.
### A9. Unused `Episode.release_id` Foreign Key
- **Location:** `app/siftarr/models/episode.py`
- **Problem:** Episodes have a `release_id` linking them to a `Release`, but this relationship is only used for clearing cache during release purge. No business logic uses it.
- **Impact:** Schema complexity, extra index, confusing ORM model.
- **Action:** Remove the `release_id` column and the associated index. Episodes should not know about releases.
### A10. Dashboard Size Display Bug
- **Location:** `TODO.md` line 14
- **Problem:** The XGB/season display in the dashboard is always red, even when the release is under any rules governing the size of a season pack.
- **Impact:** User-visible bug, breaks rule feedback UI.
- **Action:** Fix the size metadata calculation in `release_serializers.py` or the dashboard template.
### A11. `request_status_service.py` / `release_status_service.py` Overlap
- **Location:** `app/siftarr/services/release_status_service.py`, `app/siftarr/services/request_status_service.py`
- **Problem:** These modules classify release/request statuses with protocols (`EpisodeLike`) and complex logic that overlaps with `episode_sync_service.py` and `lifecycle_service.py`.
- **Impact:** Redundant code paths, maintenance burden.
- **Action:** Audit and merge into `episode_sync_service.py` or `lifecycle_service.py`. Delete the redundant modules.
---
## Category B: Overly Complex Features Needing Simplification
### B1. Plex Polling Service (6 Modules, ~900 Lines)
- **Location:** `app/siftarr/services/plex_polling_service/`
- **Problem:** The Plex polling system has incremental scans, full reconciles, targeted reconciles, identity grouping, checkpoint buffering, deduplication, and persisted job locks. For a single-instance app checking Plex, this is massively over-engineered.
- **Impact:** 6 files, 900+ lines, complex mixin inheritance, hard to test, hard to reason about.
- **Action:** Collapse into a single `PlexPollingService` class with ~3 methods:
  - `poll()` - check all active requests against Plex
  - `check_request(request_id)` - targeted check for one request
  - `scan_recent()` - check recently added items
  Remove checkpoint buffering (just scan everything every 5 minutes), remove job locks (APScheduler coalesces already), remove identity deduplication (rarely needed).
### B2. Episode Sync Service (596 Lines)
- **Location:** `app/siftarr/services/episode_sync_service.py`
- **Problem:** Complex Overseerr->Plex bridging with fallback status derivation, stale refresh logic, per-episode Plex availability, `no_autoflush` context managers, and multiple derive-status helper functions.
- **Key Constraint:** Overseerr only provides availability at the **season level**, not the episode level. If a season is currently airing or partially available, Overseerr alone cannot tell what specific episodes are missing. Therefore, accurate per-episode availability **must** come from Plex (or be calculated from known air dates if Plex is unavailable).
- **Impact:** Hard to test, brittle fallback chains, over-complicated for the actual data sources.
- **Action:** Simplify to a two-source, single-purpose flow:
  1. **Overseerr (metadata only):** Fetch seasons/episodes (titles, air dates, season count) from Overseerr and upsert into DB. **Do not use Overseerr for episode availability.**
  2. **Plex (availability only):** If Plex is configured, look up the show by TMDB/TVDB ID, get per-episode availability, and update episode statuses directly. If Plex is unavailable, leave episodes as `PENDING` (or `UNRELEASED` if air date is in the future).
  3. **Derive aggregates:** Calculate season and request status from episode statuses with simple logic (e.g., all available = available, some available = partially available, all pending = pending).
  
  Remove the `_apply_fallback_statuses` cascade entirely, remove stale-refresh logic (sync on request creation, manual refresh, and when Plex reports recent changes only).
### B3. TV Decision Service (517 Lines)
- **Location:** `app/siftarr/services/tv_decision_service.py`
- **Problem:** Parallel search with semaphore, pack candidate deduplication, season coverage calculation, episode-level status updates. Very complex for "search packs, then episodes, pick best".
- **Key Constraint:** Must avoid downloading duplicate seasons. For example, if a multi-season pack covering S01-S05 is downloaded, we must not also download a separate S03 season pack. If multi-season packs are unavailable, we should download individual season packs for each aired season (not individual episodes unless specifically requested).
- **Impact:** Hard to follow, hard to debug, the pack coverage logic is fragile.
- **Action:** Simplify the decision flow:
  - Remove parallel search semaphore management. Use `asyncio.gather` on 2-3 calls directly.
  - Remove `PackCandidate` dataclass and complex coverage deduplication.
  - **Search strategy:**
    1. If multiple seasons requested, search for a multi-season pack first.
    2. If no multi-season pack passes rules, search for individual season packs for each requested season.
    3. If individual season packs are unavailable, fallback to per-episode search (only for episodes explicitly requested or already aired).
  - **Deduplication:** Maintain a simple set of `covered_seasons`. Once a season is covered by any selected pack, do not search for or select additional releases for that season.
  - Move episode status updates to `lifecycle_service` after selection.
### B4. Settings Router Package (6 Router Modules + 4 Service Modules)
- **Location:** `app/siftarr/routers/settings/`, `app/siftarr/services/settings/`
- **Problem:** The settings router was split into a package with 6 router modules and 4 service modules. The `__init__.py` is 319 lines of delegation functions that just pass arguments through.
- **Impact:** Massive indirection, 500+ lines of boilerplate, hard to trace a request.
- **Action:** Merge back into a single `settings.py` router (~300 lines) and a single `settings_service.py`. The current split adds no value; settings pages are CRUD and simple actions.
### B5. Release Selection Service (485 Lines)
- **Location:** `app/siftarr/services/release_selection_service.py`
- **Problem:** Scoped staging (episode-level vs request-level), active selection replacement, retirement logic, `_purge_releases`, `build_prowlarr_release`, and dual staging/qBittorrent paths.
- **Impact:** Overly complex for "save releases to DB, then stage or send to qBit".
- **Action:** Split into two modules:
  - `release_storage.py` - store search results
  - `staging_actions.py` - stage/approve/send to qBittorrent
  Remove the replacement/retirement ceremony. If a user stages a new release, just delete the old staged torrent record.
### B6. Lifecycle Service with Rigid Transition Matrix
- **Location:** `app/siftarr/services/lifecycle_service.py`
- **Problem:** 368 lines defining valid transitions between 11 statuses. Most transitions are overly restrictive or unnecessary. In practice, the app forces statuses freely in many places.
- **Impact:** Boilerplate, false sense of safety, gets in the way.
- **Action:** Replace with a simple enum and allow any transition. Add a `status_history` table if audit is needed. Remove `VALID_TRANSITIONS`, `can_transition`, and all convenience methods (`mark_as_*`). Just set `request.status = X`.
### B7. Unreleased Service + Release Status Service
- **Location:** `app/siftarr/services/unreleased_service.py`, `app/siftarr/services/release_status_service.py`
- **Problem:** Two modules (~300 lines total) to determine if a movie/TV show is unreleased. Uses `EpisodeLike` protocols, `classify_movie`, `classify_tv_request`, `has_empty_seasons` checks.
- **Impact:** Over-abstracted for a simple date comparison.
- **Action:** Merge into a single function:
  ```python
  def is_unreleased(request, media_details) -> bool:
      if movie: return release_date > today
      if tv: return all(ep.air_date > today for ep in episodes)
  ```
### B8. Multiple Concurrent Settings Layers
- **Location:** `app/siftarr/config.py`, `app/siftarr/models/settings.py`, `app/siftarr/services/runtime_settings.py`
- **Problem:** Three layers of settings: Pydantic env settings, DB settings table, and `get_effective_settings()` that merges them. The DB settings table has only a handful of keys.
- **Impact:** Confusing to developers, extra DB query on every operation.
- **Action:** Remove the DB settings table and `runtime_settings.py`. Use Pydantic `BaseSettings` exclusively. For runtime toggles (like staging mode), store them in a simple JSON file or keep only the DB table and drop the env layer. Do not have both.
### B9. Dashboard API Router (724 Lines)
- **Location:** `app/siftarr/routers/dashboard_api.py`
- **Problem:** Complex serialization, `_serialize_target_scope`, `_release_matches_active_stage`, `apply_release_size_per_season_metadata`, and scattered query logic.
- **Impact:** Hard to maintain, mixes presentation logic with data fetching.
- **Action:** Extract a `DashboardService` that returns simple dataclasses. The router should only handle HTTP concerns. Move all serialization to `release_serializers.py`.
### B10. Rule Engine with TV Targeting and Compiled Regex Caching
- **Location:** `app/siftarr/services/rule_engine.py`, `app/siftarr/models/rule.py`
- **Problem:** Size limits with per-season evaluation, TV target matching (`EPISODE` vs `SEASON_PACK`), compiled regex caching. The caching is unnecessary for a ruleset that is typically <50 rules.
- **Impact:** Premature optimization, complex evaluation logic.
- **Action:** Remove compiled regex caching (`re.compile` is fast enough). Simplify size limit evaluation to just compare `release.size` against min/max without TV target branching. If TV-specific sizing is needed, make it a separate rule type.
### B11. Staging Decision Logger
- **Location:** `app/siftarr/services/staging_decision_logger.py`
- **Problem:** A dedicated module (~50 lines) just for logging staging decisions with structured messages.
- **Impact:** Minor, but unnecessary abstraction.
- **Action:** Inline the logging calls into `staged.py` router or `release_selection_service.py`. Delete the module.
### B12. Activity Log Service with Try/Except at Every Call Site
- **Location:** `app/siftarr/services/activity_log_service.py`, used everywhere
- **Problem:** Every call to `ActivityLogService.log()` is wrapped in `try/except Exception: pass`. This indicates the service is unreliable or its failures should not be fatal.
- **Impact:** Noise in every service, suggests the activity log should be fire-and-forget.
- **Action:** Make `ActivityLogService.log()` swallow its own exceptions internally. Remove all `try/except` wrappers at call sites.
---
## Category C: Database Migration Simplification
### C1. Squash All Migrations into One
- **Problem:** There are 5 migration files in 5 days. The delta migrations (`replace_size_limit_mode`, `add_denied_status`, `add_plex_scan_state`, `add_activity_log`) exist because the schema was evolving rapidly.
- **Impact:** Alembic upgrade path is fragile, `main.py` has 130 lines of compatibility hooks to patch missed migrations.
- **Action:**
  1. Delete all files in `db/alembic/versions/`.
  2. Generate a single new initial migration that creates the current schema exactly as it is today.
  3. Remove all SQLite compatibility hooks from `main.py`.
  4. Document that until v1.0, schema changes require manual DB deletion and recreation (`rm /data/db/siftarr.db && uv run alembic upgrade head`).
### C2. Remove Alembic from Startup
- **Location:** `app/siftarr/main.py:254-261`
- **Problem:** `subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)` runs on every app startup. If Alembic fails, the app crashes.
- **Impact:** Slow startup, brittle dependency on `uv` CLI inside the container, unnecessary for SQLite.
- **Action:** Remove automatic migration from `lifespan`. Make it a manual step in setup documentation. For Docker, run it in the entrypoint script, not in Python.
### C3. Remove `alembic_version` Table Management from Python
- **Location:** `app/siftarr/main.py:120-142`
- **Problem:** `_repair_missing_alembic_revision()` manually deletes and inserts alembic version rows.
- **Impact:** Dangerous, can corrupt migration state.
- **Action:** Delete. If using a single initial migration, this is irrelevant.
### C4. Simplify Migration Testing
- **Location:** `tests/test_alembic_tv_pack_migrations.py`
- **Problem:** A dedicated test for alembic migration logic.
- **Impact:** Maintenance burden for migrations that will be deleted.
- **Action:** Delete the test after squashing migrations. Add one simple test that asserts the initial migration creates all expected tables.
### C5. Remove `downgrade()` Functions
- **Location:** All migration files
- **Problem:** `downgrade()` functions are written but will never be used in SQLite pre-stable.
- **Impact:** Dead code in migration files.
- **Action:** In the single squashed migration, leave `downgrade()` as `pass` or a single `op.drop_table()` loop. Document that downgrades are not supported.
---
## Category D: General Architecture Improvements
### D1. Status Storage and Plex Scanning Strategy
- **Problem:** 11 statuses (`RECEIVED`, `SEARCHING`, `PENDING`, `UNRELEASED`, `STAGED`, `DOWNLOADING`, `COMPLETED`, `FAILED`, `AVAILABLE`, `PARTIALLY_AVAILABLE`, `DENIED`). Many are semantically overlapping.
- **Clarification:** The user notes that statuses are stored so we do not have to continually fetch state from Plex. This is a valid performance concern. Rather than eliminating stored statuses entirely, we should make the Plex scanning smarter.
- **Action:**
  - Keep stored per-episode and per-request statuses, but simplify the enum:
    - Core workflow statuses: `PENDING`, `SEARCHING`, `STAGED`, `DOWNLOADING`, `COMPLETED`, `FAILED`
    - Keep `UNRELEASED` as a stored status (it prevents unnecessary searches and is not derivable from Plex)
    - Keep `DENIED` as a terminal state
    - Remove `AVAILABLE` and `PARTIALLY_AVAILABLE` as stored statuses. These should be derived from episode statuses at query time (e.g., a request is "available" when all its episodes are `COMPLETED` or `AVAILABLE`). If the UI needs these labels, compute them in the serializer.
  - Use Plex's **recently added/changed endpoints** to drive incremental scans instead of blindly polling all active requests. Store a `last_plex_check_at` timestamp on the request. Only re-scan requests whose related Plex items have changed since that timestamp.
  - This eliminates the need for checkpoint buffering and full-reconcile complexity in the polling service.
### D2. Merge `pending_queue` Table into `requests`
- **Problem:** A separate `pending_queue` table with retry count and next retry timestamp.
- **Action:** Add `next_retry_at` and `retry_count` columns directly to `requests`. Remove the table and `PendingQueueService`.
### D3. Merge `plex_scan_state` Table into Memory
- **Problem:** A database table for job locks and checkpoints for a single-process app.
- **Action:** Store job state in memory (a dict on `SchedulerService`). Delete the table and `PlexScanStateService`.
### D4. Replace Mixin Inheritance with Composition
- **Problem:** `PlexPollingService` inherits from 5 mixins (`ProbeMixin`, `IdentityMixin`, `TargetedReconcileMixin`, `IncrementalScanMixin`, `FullReconcileMixin`). `PlexService` inherits from 4 mixins.
- **Action:** Use composition. Inject helper classes or plain functions.
### D5. Remove `requested_seasons` / `requested_episodes` JSON Strings
- **Problem:** `Request.requested_seasons` and `requested_episodes` store JSON in string columns.
- **Action:** Use proper related tables or native JSON columns (SQLite 3.38+ supports JSON). Better yet, since `Season` and `Episode` tables exist, derive requested seasons from those relationships.
---
## Recommended Execution Order
1. **C1-C5:** Squash migrations and remove compatibility hooks. This unblocks everything else.
2. **A1-A2, B12:** Fix bugs and cleanup (redirect loop, empty finally blocks, activity log wrappers).
3. **B6, D1, D2, D3:** Simplify data model (statuses, pending queue, scan state).
4. **B1-B2:** Simplify Plex polling and episode sync.
5. **B3, B5:** Simplify TV decision and release selection.
6. **B4:** Merge settings package back to single file.
7. **B7-B11:** Remove small unnecessary abstractions.
8. **A3, A5-A12:** Remove incomplete features and schema clutter.
9. **B8, D4-D5:** Architecture improvements (settings layers, mixins, JSON columns).
10. **Tests:** After each phase, delete obsolete tests and add focused ones for the simplified code.
---
## Files to Delete (High Confidence)
| File | Reason |
|------|--------|
| `app/siftarr/repair_request_state.py` | Unused CLI utility |
| `db/alembic/versions/2026_04_16_2200_*.py` | Delta migration (squash) |
| `db/alembic/versions/2026_04_17_0800_*.py` | Delta migration (squash) |
| `db/alembic/versions/2026_04_19_1200_*.py` | Delta migration (squash) |
| `db/alembic/versions/2026_04_20_0100_*.py` | Delta migration (squash) |
| `app/siftarr/services/staging_decision_logger.py` | Inline the logging |
| `app/siftarr/services/release_status_service.py` | Merge into episode sync |
| `app/siftarr/services/request_status_service.py` | Merge into lifecycle |
| `tests/test_alembic_tv_pack_migrations.py` | Obsolete after squash |
| `app/siftarr/services/settings/` (4 modules) | Over-abstracted |
| `app/siftarr/routers/settings/` (6 modules) | Over-abstracted |
| `app/siftarr/services/plex_polling_service/` | Collapse to single file |
| `app/siftarr/services/plex_service/` | Collapse to single file |
## Estimated Line Count Reduction
- Current app code: ~8,500 lines (excluding tests)
- After simplification: ~4,500-5,500 lines
- Reduction: ~35-45%
This plan prioritizes **deletion over modification** and **simplicity over flexibility** to match the project's pre-stable state.

---
## Refactoring Waves (Recommended Execution)
The following organizes all improvements into **4 waves**. Each wave is designed to be self-contained, pass all quality gates independently, and unblock the next wave. Do not start a new wave until the previous one is merged.
### Wave 1: Foundation & Safe Cleanup
**Theme:** Delete dead code, fix bugs, and establish a clean migration baseline.
**Why first:** This wave removes ~1,500 lines of compatibility hooks, dead code, and unused schema fields with minimal business logic risk. Squashing migrations first unblocks all subsequent schema changes.
**Items:**
- **C1-C5:** Squash all migrations into one, remove alembic from startup, delete compatibility hooks.
- **A1-A2:** Fix root redirect loop and empty `finally: pass` blocks.
- **A5-A7:** Delete legacy SQLite compatibility hooks (`_prepare_legacy...`, `_repair_missing...`, etc.), remove `_poll_plex_availability` wrapper, delete `repair_request_state.py`.
- **A8-A10:** Remove unused `Release.is_downloaded`/`downloaded_at`, remove `Episode.release_id`, fix dashboard size display bug.
- **A11:** Merge `release_status_service.py` and `request_status_service.py` into `lifecycle_service.py` or `episode_sync_service.py`.
- **B12:** Make `ActivityLogService.log()` swallow its own exceptions; remove `try/except` wrappers at all call sites.
**Validation:** `uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest`
---
### Wave 2: Data Model Simplification
**Theme:** Simplify the core data model and status definitions.
**Why second:** These changes alter the database schema and core enums. They must happen after migrations are stable but before the big service rewrites depend on them.
**Items:**
- **D2:** Merge `pending_queue` table into `requests` (add `next_retry_at`, `retry_count` columns).
- **D3:** Merge `plex_scan_state` table into in-memory state on `SchedulerService`.
- **B6:** Simplify `LifecycleService` — remove `VALID_TRANSITIONS` matrix, `can_transition()`, and `mark_as_*` convenience methods. Allow direct status assignment.
- **D1:** Simplify `RequestStatus` enum: keep `PENDING`, `SEARCHING`, `STAGED`, `DOWNLOADING`, `COMPLETED`, `FAILED`, `UNRELEASED`, `DENIED`. Remove `AVAILABLE` and `PARTIALLY_AVAILABLE` as stored statuses — derive them from episode states at query time. Add `last_plex_check_at` to `Request`.
- **D5:** Remove `requested_seasons` and `requested_episodes` JSON string columns from `Request`. Derive from `Season`/`Episode` relationships instead.
- **B8:** Collapse settings layers — remove DB `settings` table and `runtime_settings.py`. Use Pydantic `BaseSettings` exclusively for configuration.
**Validation:** Same quality gates. Verify that pending queue retry, scheduler jobs, and status transitions still work.
---
### Wave 3: Plex & Sync Services
**Theme:** Collapse over-engineered Plex services and simplify episode sync.
**Why third:** These are the largest service rewrites (~1,500 lines). They depend on the simplified status enum and data model from Wave 2. The Plex polling mixins reference `PlexScanStateService` (deleted in Wave 2) and `RequestStatus` values.
**Items:**
- **B1:** Collapse `plex_polling_service/` (6 modules, ~900 lines) into a single `plex_polling_service.py` with `poll()`, `check_request()`, and `scan_recent()`. Remove checkpoint buffering, job locks, and identity deduplication.
- **B2:** Simplify `episode_sync_service.py` to the two-source flow: Overseerr for metadata only, Plex for per-episode availability only. Remove `_apply_fallback_statuses`, stale-refresh logic, and `no_autoflush` workarounds.
- **D4:** Replace mixin inheritance with composition for both `PlexPollingService` and `PlexService`.
- **B7:** Merge `unreleased_service.py` and `release_status_service.py` into a single `is_unreleased()` utility function in `episode_sync_service.py` or `lifecycle_service.py`.
**Validation:** Same quality gates. Verify that Plex polling, episode sync, and unreleased detection still pass their test suites.
---
### Wave 4: Decision Engine, Settings & UI
**Theme:** Simplify the remaining business logic and UI layers.
**Why fourth:** These are the most user-facing changes. The TV decision service depends on the simplified `RequestStatus` and `Release` model. The settings router merge is independent but low priority.
**Items:**
- **B3:** Simplify `tv_decision_service.py`:
  - Remove `PackCandidate` dataclass and semaphore management.
  - Implement multi-season pack → individual season pack → per-episode fallback search.
  - Use a simple `covered_seasons` set to prevent duplicate downloads.
- **B5:** Split `release_selection_service.py` into `release_storage.py` and `staging_actions.py`. Remove retirement/replacement ceremony.
- **B4:** Merge `routers/settings/` (6 modules + `__init__.py`) back into a single `settings.py` router. Merge `services/settings/` (4 modules) into a single `settings_service.py`.
- **B9:** Extract `DashboardService` from `dashboard_api.py`. Move serialization logic out of the router.
- **B10:** Simplify `rule_engine.py` — remove compiled regex caching and TV-target size limit branching.
- **B11:** Inline `staging_decision_logger.py` into `staged.py` or `release_selection_service.py`.
- **A3:** Implement or remove the TV general search button. If implementing, add season/episode/multi-season pack scope selection.
**Validation:** Same quality gates. Run the full test suite including router tests.
---
### Cross-Wave Rules
1. **One wave per branch:** Create a feature branch for each wave (e.g., `refactor/wave-1-foundation`).
2. **Quality gates between waves:** All 4 quality gates must pass before merging a wave.
3. **Delete obsolete tests immediately:** After each wave, delete tests for deleted modules and add focused tests for new simplified code.
4. **No schema changes after Wave 2:** Waves 3 and 4 should not alter the database schema. If a schema change is discovered as needed, add it to Wave 2 first.
5. **Preserve behavior:** Each wave should be a pure refactor with no user-visible behavior changes (except A3 in Wave 4, which is a feature fix).