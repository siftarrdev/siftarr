## New Features
- Add option to only apply rules to requests from certain users or groups
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Rework the general TV search button in the details modal. The current implementation shows a "not fully implemented" toast. It needs to be redesigned to support different search strategies depending on context: searching for a specific season, individual episodes, or multi-season packs. The UI should let the user choose what scope to search (e.g., "Search Season X", "Search All Episodes", "Search Multi-Season Packs") rather than a single generic search button.

- Add request timeline/audit view per request, that shows when it was requested, and any actions taken on it (e.g., when it was marked as available, when it was staged, when it was sent to qBit, etc.). for tv shows this should be per season/episode, and for movies it should be per movie. This will help users understand the history of a request and troubleshoot any issues. It should be at the bottom of the details view for each request.
- we need a mark episode as available button in the details view. There should be a button for each episode, and then a general button for "Mark All Available". This will allow users to manually indicate that certain episodes are available through other means (e.g., already have the episode, found it on another platform, etc.) so that the system doesn't keep trying to find it. This is useful for cases where there might be two part episodes, but the season pack has them as one file rather than separate episodes.

## Project quality improvements
- Improve error handling so the app can gracefully handle unexpected errors and provide useful feedback
- Tighten router input validation so bad payloads fail loudly
- Split large router modules into thinner handlers plus service methods
- Add integration tests for the full webhook -> search -> rule evaluation -> staging/qBit flow
- Increase unit test coverage to 85% (currently at 47%; focus on router integration tests and complex async service tests)

- Add structured decision logs/metrics for search latency, rule failures, pending retries, and qBit handoff


## Bugs
- the Xgb/season display in the dashboard is always showing red, even when the release is under any rules governing the size of a season pack.

- on the staged/downloading tab it seems like the downloading detection and finished detection isn't great. probably for torrents that are in this tab we should more actively check from qbittorrent and plex to update status.