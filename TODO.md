## New Features
- Add option to only apply rules to requests from certain users or groups
- Add request timeline/audit view per request
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Make settings sections collapsible for easier navigation
- fix teh design of the setting buttons for plex|overseer sync etc. Also make the Setup sections for all the services we plug into more compact and add in the plex setup section as well, as currently it is just using the env vars and doesn't have a UI for it.
- add a popup with progress bar when manually syncing the overseer, so the user has feedback that something is happening and the app isn't frozen
- Implement search result caching so that past search results are displayed immediately. new searches could be done in the background and or on demand whent he user click the search button again.
- Rework the general TV search button in the details modal. The current implementation shows a "not fully implemented" toast. It needs to be redesigned to support different search strategies depending on context: searching for a specific season, individual episodes, or multi-season packs. The UI should let the user choose what scope to search (e.g., "Search Season X", "Search All Episodes", "Search Multi-Season Packs") rather than a single generic search button.


## Project quality improvements
- Improve error handling so the app can gracefully handle unexpected errors and provide useful feedback
- Tighten router input validation so bad payloads fail loudly
- Split large router modules into thinner handlers plus service methods
- Add integration tests for the full webhook -> search -> rule evaluation -> staging/qBit flow
- Add structured decision logs/metrics for search latency, rule failures, pending retries, and qBit handoff
- Increase unit test coverage to 85% (currently at 47%; focus on router integration tests and complex async service tests)


## Bugs
- exclusion pattern matches shouldn't report as an error in the dashboard
- send logs for rejected release when other release already sent to qbittorrent. also the sent release is downloaded by the torrent is still in staging.
- the Xgb/season display in the dashboard is always showing red, even when the release is under any rules governing the size of a season pack.