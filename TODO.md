# TODO

This file captures the biggest opportunities identified during a repo-wide review.

## Performance improvements

1. **Batch TV lifecycle queries**
   Reduce N+1-style loading in lifecycle and episode/season status checks by using joined or select-in loading, and prefer set-based updates where possible.

2. **Avoid full release reloads in detail views**
   The request detail page should avoid re-reading or re-grouping the entire release set when only a paginated subset is needed.

3. **Add composite indexes for common release filters**
   Add indexes for request-scoped sorting/filtering such as resolution, score, seeders, and publish date.

4. **Reuse stored release evaluation results**
   If score/rule outcomes already exist in the database, serialize those values directly unless the rule version changed.

5. **Cache stats responses by date range**
   Stats aggregation is a good candidate for short-lived caching, especially for repeated dashboard refreshes.

6. **Consolidate stats aggregation queries**
   Combine repeated aggregate reads into fewer SQL calls, or parallelize independent queries where it is safe to do so.

7. **Tighten broad TV pack searches**
   Add better limits, category narrowing, or query shaping so broad searches return less noise.

8. **Bulk release storage operations**
    Use bulk insert/update/delete patterns for large search result sets instead of row-by-row Python loops.

9. **Bound season sweep concurrency**
    Add concurrency limits to season-wide TV searches to reduce upstream pressure and flapping.

## Simplification / refactor opportunities

1. **Split the TV decision service**
   Break the large TV decision module into smaller parts for target selection, search orchestration, pack handling, and persistence.

2. **Split the staging service**
   Separate torrent file handling, staging DB logic, qBit handoff, and state transitions into focused helpers/services.

3. **Centralize deduplication logic**
   Use one canonical helper for release identity and dedup keys across search, storage, and decision flows.

4. **Unify rule-engine loading**
   Share a single rule-engine provider instead of rebuilding cache/access patterns in several modules.

5. **Unify movie and TV decision pipelines**
   Keep media-specific selection logic separate, but share the common search → evaluate → persist → stage flow.

6. **Move serialization into schemas**
   Replace manual dashboard DTO assembly with response schemas or dedicated serializer helpers.

7. **Extract SSE orchestration helpers**
   Move search-streaming state, queue handling, and execution orchestration out of the router layer.

8. **Split large dashboard templates**
   Break tables, modals, and details into reusable partials/macros to reduce template complexity.

9. **Modularize dashboard JavaScript**
   Separate API access, state management, rendering, and event wiring into smaller JS modules.

10. **Centralize lifecycle status semantics**
    Define one shared source of truth for active, terminal, and actionable request statuses.

11. **Replace string literals with enums/constants**
    Standardize search modes, sources, and staged statuses to reduce drift and typo risk.

## New feature ideas

1. **Per-indexer controls**
   Let admins enable/disable indexers and assign priorities so the search stack can be tuned more precisely.

2. **Rule dry-run simulator**
   Preview how rule changes would affect existing releases before saving them.

3. **Notifications**
   Send events to Discord, Slack, or generic webhooks when items stage, fail, complete, or need attention.

4. **Per-request profile overrides**
   Override global quality/rule behavior for specific requests when needed.

5. **Manual lifecycle recovery actions**
   Add buttons for retry, reset to pending, or mark failed for operator recovery.

6. **Search cost dashboard**
   Display query volume, cache hit rate, latency, and error trends.

7. **Multi-user roles**
   Expand beyond a single admin model to viewer/operator/admin-style permissions.

8. **Path/category templates**
    Support templated save paths and qBit categories based on media metadata.

9. **Release blacklist/whitelist**
    Allow blocking or preferring release groups, uploaders, indexers, or keywords.

## Existing feature expansion ideas

1. **Improve TV full-search UX**
   Surface exact episode and season-pack progress in live search streaming views.

2. **Add more release filters**
   Support filtering by indexer, codec, group, seeders, size, age, pass/fail, and more.

3. **Bulk TV operations**
   Add batch actions for full search, availability marking, and seasonal resets.

4. **More pending retry controls**
   Make retry intervals and pause/resume behavior configurable per request or queue item.

5. **Plex sync visibility**
   Show last check time, matched items, and sync failures directly in the UI.

6. **qBit move/retention UI**
   Expose move history, failed move retries, and retention controls in a readable dashboard view.

7. **Overseerr sync diagnostics**
    Display webhook/import attempts and reconciliation state for requests.

8. **Search cache management**
    Add cache stats and cache clear/invalidate actions.

9. **Movie matching controls**
    Add configurable tolerance and clearer mismatch explanations for movie lookup/parsing.
