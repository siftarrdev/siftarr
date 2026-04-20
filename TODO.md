## New Features
- Add option to only apply rules to requests from certain users or groups
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Rework the general TV search button in the details modal. The current implementation shows a "not fully implemented" toast. It needs to be redesigned to support different search strategies depending on context: searching for a specific season, individual episodes, or multi-season packs. The UI should let the user choose what scope to search (e.g., "Search Season X", "Search All Episodes", "Search Multi-Season Packs") rather than a single generic search button.

## Project quality improvements
- Improve error handling so the app can gracefully handle unexpected errors and provide useful feedback
- Tighten router input validation so bad payloads fail loudly
- Split large router modules into thinner handlers plus service methods
- Add integration tests for the full webhook -> search -> rule evaluation -> staging/qBit flow
- Increase unit test coverage to 85% (currently at 47%; focus on router integration tests and complex async service tests)


## Bugs
- the Xgb/season display in the dashboard is always showing red, even when the release is under any rules governing the size of a season pack.

- TV show releases are not correctly being marked as available when Plex detects them. Unless i do a manual sync in plex.
- when staging multiple episodes for the same season, if i mark one as staged, all other episodes come up witht eh replace stged button. Each episode should be able to be staged independently, and only show the "replace staged" button if that specific episode is already staged. Dont worry about any complex logic for season packs or multi-episode releases, just make sure the button logic is per-episode.
- since qbittorrent is my own server app. maybe we could check the status/% if the download every 30s and trigger the plex check as soon as it shows 100%? That way we can update availability faster without waiting for the next plex scan or a manual check.