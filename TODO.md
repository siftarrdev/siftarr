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

## Bugs
- the requested on date seems to be the date the request was loaded from overseerr rather than the date the request was made.