## New Features
- Add option to only apply rules to requests from certain users or groups
- Add request timeline/audit view per request
- Add manual "choose another release" UI to re-run selection from stored candidates
- Add per-media-type download path and category overrides instead of fixed qBit categories
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Make settings sections collapsible for easier navigation
- Increase unit test coverage to 85% (currently at 47%; focus on router integration tests and complex async service tests)

## Project quality improvements
- Refactor the codebase for better separation of concerns and more consistent coding style
- Improve error handling so the app can gracefully handle unexpected errors and provide useful feedback
- Replace startup schema-repair hacks with a single explicit migration/compatibility path
- Tighten router input validation so bad payloads fail loudly
- Split large router modules into thinner handlers plus service methods
- Add integration tests for the full webhook -> search -> rule evaluation -> staging/qBit flow
- Add structured decision logs/metrics for search latency, rule failures, pending retries, and qBit handoff

## Performance improvements
- Precompile rule regexes once per rule set instead of recompiling on every release evaluation
- Reuse a shared `httpx.AsyncClient` for Prowlarr/Overseerr instead of creating a new client per request
- Replace Python-side full-table scans with SQL aggregates for settings stats and pending queue stats
- Add DB indexes on hot filters like `requests.status`, `releases.request_id`, and `pending_queue.next_retry_at`
- Avoid fetching Overseerr status for every active request on every dashboard load; cache or lazy-load those lookups
