## New Features
- Add option to only apply rules to requests from certain users or groups so certain peoples requests get filled at 4k while others get filled at 1080p. This would be a user or group based preference that can be set in the rules.
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Download history & stats - Track completed downloads, show indexer performance metrics, rule effectiveness stats.
- Bulk staging actions - Approve/reject multiple staged torrents at once from the dashboard
- Custom post-processing hooks - Execute user-defined scripts when downloads complete (for renaming, notifications, etc.)
- Authentication & Authorization — Zero auth on any endpoint today. Add API-key header auth at minimum; ideally integrate with Overseerr's SSO or support basic auth. This is a critical gap

## Performance Improvements

1.  **Cache rule engines/compiled regex by enabled rule set and media type** — `MovieDecisionService`/`TVDecisionService` load all rules and `RuleEngine.__init__` recompiles patterns per request. Cache the built engine or compiled patterns and invalidate on rule changes.
2.  **Batch pending-queue transactions** — `PendingQueueService.add_to_queue`, `remove_from_queue`, `mark_retry_failed`, and `update_error` commit per item; scheduler loops call them repeatedly. Add commit-control or bulk helpers so scheduled jobs can update many rows in one transaction.
3.  **Deduplicate TV search releases before evaluation/storage** — TV flows can find the same release via broad, season, and episode searches. Deduplicate by `info_hash`, magnet URL, download URL, or title/indexer before rule evaluation and persistence.
4.  **Avoid repeated release coverage parsing** — `parse_release_coverage()` is called by rule evaluation, TV selection helpers, and serializers. Compute once per release and pass/cache the parsed coverage through evaluation and serialization.
5.  **Reduce per-torrent qBittorrent lookups** — `DownloadCompletionService.check_downloading_requests` calls qBit once or more per approved torrent. Fetch the torrent list once per cycle and match locally by hash/name.
6.  **Reuse the shared HTTP client for torrent downloads** — `StagingService.save_release` and `TorrentService` create new `httpx.AsyncClient()` instances instead of using `http_client.get_shared_client()`.
7.  **Run broad TV fallback searches concurrently** — `ProwlarrService._broad_tv_search` performs three independent title queries sequentially. Use bounded `asyncio.gather` and preserve deduplication.
8.  **Make Prowlarr search caching request-aware** — identical TMDB/TVDB/title searches can repeat during manual refreshes and retries. Add a short TTL cache, but only for safe search result payloads and with invalidation/disable controls for retry debugging.
    - **Design considerations**: A naive in-memory cache with TTL is risky because Prowlarr indexers update continuously — cached results may miss newly appeared releases, which defeats the purpose of a retry. Consider the following approach instead:
      - Cache keyed on `(query_type, query_id, categories)` with a very short TTL (30–60s), only effective within a single scheduler cycle or burst of manual actions.
      - Only cache successful 200 responses; don't cache errors or partial results.
      - Add a `SIFTARR_DISABLE_SEARCH_CACHE` env var and a settings toggle to bypass the cache entirely (essential for debugging retries).
      - Invalidate on any rule change (since different rules may select different results from the same set).
      - Consider scoping the cache to the scheduler's pending-queue loop only — manual dashboard searches should always hit Prowlarr fresh since the user explicitly triggered them.
      - An LRU-bounded `dict` (max ~50 entries) avoids unbounded memory growth vs. `functools.lru_cache` which doesn't support TTL natively.
9.  **Narrow Plex polling candidate sets** — `PlexPollingService.poll()` and `scan_recent()` load all non-terminal requests. Prefer recently changed/download-related requests for frequent scans, with a less frequent full reconcile.
10. **Verify and add DB indexes for dashboard/background hot paths** — Existing indexes: `requests` (status, media_type, created_at, next_retry_at), `releases` (request_id, score), `activity_logs` (request_id, created_at), `episodes` (season_id), `seasons` (request_id). **Missing indexes**: `staged_torrents.request_id`, `staged_torrents.status`, `rules.type`, `rules.enabled`, `activity_logs.event_type`. The `staged_torrents` and `rules` tables have zero indexes — these are hit on every dashboard load and staging action.
11. **Lazy-load release pagination on dashboard** — `DashboardService.load_request_details()` loads *all* releases for a request. Paginate at 50–100 rows; most users only care about top-scored candidates.
12. **Pre-compute dashboard stats** — `get_requests_stats()` runs SQL aggregates on every settings page load. Cache in-memory or use a materialized counter table updated by triggers.
13. **Fix N+1 queries in dashboard route** — `dashboard()` calls `_should_show_in_unreleased()` and `_tv_has_pending_episodes()` per request in a loop, each firing separate DB queries for seasons/episodes. Pre-fetch with a single bulk query or `selectinload`.
14. **Batch qBittorrent API calls in completion service** — `DownloadCompletionService.check_downloading_requests` queries qBit per approved torrent (each an HTTP round-trip via `asyncio.to_thread`). Fetch the full torrent list once per cycle with `torrents_info()` and match locally by hash.
15. **Fix `http_client.py` duplicate `DEFAULT_LIMITS` constant** — Lines 9 and 17 both define `DEFAULT_LIMITS` with different values; the second silently shadows the first. This is a bug causing unexpected connection limits. Consolidate to one definition.
16. **Avoid full-table release churn on re-search** — `release_storage.store_search_results()` deletes all releases for a request then bulk-inserts. For frequent re-searches, this causes table-level churn. An upsert/diff approach (matching by `download_url` or `info_hash`) would reduce write amplification and preserve stable row IDs.

## Areas for Simplification

1.  **Extract shared decision pipeline steps carefully** — movie and TV decisions share rule loading, evaluation, release storage, pending handling, staging/handoff, and logging. Extract shared helpers or a small pipeline, but keep TV-specific season/episode selection out of a generic base class.
2.  **Split `DashboardService` by responsibility** — it mixes request detail DTOs, stored-release loading, manual Prowlarr searches, TV enrichment, Overseerr details, and timeline loading. Split into detail, search, TV enrichment, and metadata/timeline services.
3.  **Reduce settings-router wrapper bloat** — `routers/settings.py` contains many pass-through wrappers around `settings_service` to inject dependencies. Prefer direct service calls, smaller dependency objects, or focused orchestration classes.
4.  **Move in-method imports to module scope where possible** — e.g. `MovieDecisionService` imports `EventType` inside `process_request`; `scheduler_service` imports `DownloadCompletionService` inside a method. Keep local imports only where needed to avoid cycles.
5.  **Consolidate duplicated `_utc_now()` helpers** — the same timestamp helper appears in several model files (`request`, `release`, `season`, `activity_log`, `rule`, `staged_torrent`). Move to a shared model utility.
6.  **Remove logging workarounds after fixing logging setup** — `_log()` helpers in `download_completion_service.py` and `episode_sync_service.py` duplicate logger fallback behavior. Standardize logging configuration and remove custom helpers.
7.  **Replace `_maybe_await()` test accommodation with clearer interfaces** — `rules.py` supports sync test doubles in async paths. Prefer async protocols/mocks or explicit adapters.
8.  **Use typed result objects for Plex scheduler metrics** — `scheduler_service` currently duck-types Plex job results in `_build_plex_job_metrics_payload()` and `_get_plex_completed_requests()`. A protocol or common result DTO would simplify this.
9.  **Move release-state/unreleased responsibilities behind `unreleased_service`** — lifecycle state transitions and unreleased detection are spread across lifecycle/scheduler/unreleased services. Keep generic status transitions in lifecycle, and release-date/availability decisions in `unreleased_service`.
10. **Make integration lifecycle management consistent** — many call sites instantiate services then manually `finally: await service.close()`, even when close is a no-op for shared clients. Use async context managers or dependency-injected service factories.
11. **Make `StagingService` require a database session** — it accepts `AsyncSession | None` but most methods immediately raise if missing. Make `db` required and isolate any file-only/test helpers separately.
12. **Consolidate release handoff boundaries** — `staging_service.py`, `staging_actions.py`, and `torrent_service.py` overlap around staged/download handoff. Define one orchestration boundary for “use selected release(s)”.

13. **Simplify request status transition logic** — `LifecycleService.transition()` commits, logs, then commits again. Combine into one transaction; the double commit is unnecessary and risks inconsistency under load.
14. **Simplify Plex polling abstraction stack** — `PlexPollingService`, `EpisodeSyncService`, and `PlexService` have overlapping responsibilities for lookup, availability, and reconciliation. Merge lookup into `PlexService` and make polling a thin state-machine wrapper.
15. **Unify configuration mutation** — `_set_db_setting` mutates `os.environ` at runtime and clears an `lru_cache`. This is fragile and hard to trace. Use a proper settings store (database-backed with pydantic validation) rather than mutating the process environment.
16. **Fix `activity_log_service.py` rollback risk** — `ActivityLogService.log()` calls `await self.db.rollback()` on failure (line 42), which rolls back the *entire* parent transaction, not just the log entry. Use a nested transaction (savepoint via `begin_nested()`) so that a logging failure doesn't destroy the caller's work.
17. **Move `_process_request_search` from router to service** — This private function in `dashboard_actions.py` is imported by both `dashboard_api.py` and `search_sse.py`. Search orchestration is business logic, not routing logic; it belongs in a service (e.g., `search_service.py`).
18. **Fix `database.py` module-level engine creation** — The engine is created at module import time (line 357) before the lifespan manager runs. This can cause race conditions with startup initialization. Defer engine creation to `init_db()` or use a lazy property.
19. **Add authentication to all endpoints** — Zero auth on any endpoint today. Anyone with network access can trigger searches, deny requests, modify rules, clear caches, and read API keys. At minimum, add API-key header auth; ideally integrate with Overseerr's SSO or support basic auth.
20. **Protect API key exposure in connections endpoint** — `/settings/api/connections` returns all API keys (Overseerr, Prowlarr, qBittorrent, Plex) in plaintext JSON. Combined with no authentication, this is a complete credential leak. Mask keys in responses; never return full secrets.

## Bugs
