## New Features
- Add option to only apply rules to requests from certain users or groups
- Add request timeline/audit view per request
- Add manual "choose another release" UI to re-run selection from stored candidates
- Add per-media-type download path and category overrides instead of fixed qBit categories
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Make settings sections collapsible for easier navigation
- Increase unit test coverage to 85% (currently at 47%; focus on router integration tests and complex async service tests)
- Add a year column to all the tables in the dashboard for easier sorting and scanning of recent activity
- make the search results in the request details view much more compact by optimising the use of space and removing redundant information, allowing more results to be visible at once without scrolling

## Project quality improvements
- Refactor the codebase for better separation of concerns and more consistent coding style
- Improve error handling so the app can gracefully handle unexpected errors and provide useful feedback
- Replace startup schema-repair hacks with a single explicit migration/compatibility path
- Tighten router input validation so bad payloads fail loudly
- Split large router modules into thinner handlers plus service methods
- Add integration tests for the full webhook -> search -> rule evaluation -> staging/qBit flow
- Add structured decision logs/metrics for search latency, rule failures, pending retries, and qBit handoff

## Performance improvements

