<!-- Stats persistence contract; API/UI implementation is intentionally deferred. -->

# Stats Metrics Audit and Contract

## Scope and date semantics

- Stats support all-time and date-ranged views.
- Ranged views use `activity_logs.created_at` for event-based metrics and `requests.created_at` for request intake totals.
- `downloads_processed` means approved requests: count `release_approved` events, de-duplicated by `request_id` within the selected range. All-time uses the same definition without a date filter.
- Existing historical rows must expose unavailable metrics as unavailable/partial, not inferred.

## Current persistence support

| Requested metric | Current support | Notes |
| --- | --- | --- |
| Total requests | Supported | `requests.created_at`, `media_type`, and `status` support all-time/ranged intake cards. |
| Downloads processed | Partially supported | `activity_logs.release_approved` and `staged_torrents.status='approved'` show approvals. Event de-duplication by `request_id` is needed; direct-send non-staging paths do not emit `release_approved`. |
| Approval rate | Partially supported | Numerator: de-duplicated approved requests. Denominator proposal: requests with a completed search/evaluation in range. Current logs make this approximate because searches can repeat and direct-send approvals are not consistently logged. |
| Resolution split | Blocked for durable approved-download stats | `releases.resolution` exists for candidate releases and staging JSON contains resolution, but `staged_torrents` does not persist resolution and JSON sidecars are removed on approval. |
| Source/indexer split | Partially supported | `releases.indexer` and `staged_torrents.indexer` exist. Approved event details currently omit indexer, so durable event-scoped source split should be persisted at approval time. |
| Processing times | Partially supported | `search_started`/`search_completed`, `release_staged`, `release_approved`, and status-change events can approximate intervals, but no durable duration fields or correlation IDs protect against repeated searches/retries. |
| Rule outcomes | Partially supported | Rule definitions exist; candidate `releases.passed_rules`, `score`, and `rejection_reason` are stored. Per-rule evaluation outcomes are only transient `ReleaseEvaluation.matches` and are not persisted. Aggregate `rule_evaluation` logs only counts. |

## Metric contract

- Cards:
  - total requests: count requests created in range.
  - downloads processed: count distinct requests approved in range.
  - approval rate: `downloads_processed / evaluated_requests`, where `evaluated_requests` is distinct requests with a completed decision/search evaluation in range.
  - median/p95 processing times where durable timing data exists.
- Charts:
  - filled resolution split: buckets `4K`, `1080p`, `other`; map `2160p`/`4k` to `4K`, `1080p` to `1080p`, everything else or missing to `other` only when a selected approved release is known.
  - source split: approved downloads by selected release indexer.
  - processing times: search duration and request-to-approval duration.
  - rule outcomes: counts by rule and outcome (`matched`, `not_matched`, `passed`, `failed/rejected`, score delta where relevant).

## Phase 2 approved persistence

Approved long-term approach: dedicated immutable metrics tables. Existing historical rows are not backfilled with fabricated data; stats API work should expose unsupported/partial fields as unavailable when no metric rows exist.

Tables:

1. `stats_release_facts`: one immutable row per newly approved selected release, with request/release/staged IDs where available, title, indexer, raw resolution, bucket (`4K`, `1080p`, `other`), selection source, and approval timestamp.
2. `stats_rule_outcomes`: per-release/per-rule evaluation outcomes for new search/evaluation runs, including matched flag, outcome, score delta, rejection reason, and stored release ID where available.
3. `stats_timing_events`: durable timing events emitted alongside activity logs for search/rule/stage/approval/download events, plus computed `request_to_approval` duration rows when a selected release is approved.

Instrumentation covers automatic decisions, manual release selections, staged approvals/replacements, direct-send approvals, and activity-log timing events. No Stats router/API/UI is included in Phase 2.
